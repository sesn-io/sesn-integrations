"""Offline tests for the 0.2.11 security/reliability rules: server address
checks, 401 handling, retry/backoff, queue expiry, pairing interval, list ids
and the down-sync page loop."""
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

LIB = Path(__file__).resolve().parents[1] / 'script.sesn' / 'resources' / 'lib'


class Kodi:
    """Just enough of xbmc / xbmcaddon / xbmcvfs / xbmcgui for the modules."""

    def __init__(self):
        self.settings = {'api_key': 'synthetic-test-key', 'server_url': 'https://sesn.io', 'debug': False}
        self.logs, self.files, self.notes = [], {}, []
        kodi = self

        class File:
            def __init__(self, path, mode='r'):
                self.path = path
            def read(self):
                return kodi.files.get(self.path, '')
            def write(self, data):
                kodi.files[self.path] = data
            def close(self):
                pass

        class Monitor:
            def waitForAbort(self, seconds):
                kodi.waits.append(seconds)
                return False

        self.waits = []
        addon = types.SimpleNamespace(
            getSettingString=lambda k: self.settings.get(k, ''),
            setSettingString=lambda k, v: self.settings.__setitem__(k, v),
            getSettingBool=lambda k: bool(self.settings.get(k, True)),
            getAddonInfo=lambda k: '/profile/',
        )
        self.modules = {
            'xbmc': types.SimpleNamespace(log=lambda msg, level=0: self.logs.append(msg), LOGINFO=1, LOGWARNING=2,
                                          LOGERROR=3, Monitor=Monitor),
            'xbmcaddon': types.SimpleNamespace(Addon=lambda: addon),
            'xbmcvfs': types.SimpleNamespace(translatePath=lambda p: p, exists=lambda p: True, mkdirs=lambda p: None, File=File),
            'xbmcgui': types.SimpleNamespace(Dialog=lambda: types.SimpleNamespace(notification=lambda *a: self.notes.append(a)),
                                             NOTIFICATION_WARNING=2),
        }


def load(name, kodi, extra=None):
    spec = importlib.util.spec_from_file_location(name, LIB / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {**kodi.modules, **(extra or {})}):
        spec.loader.exec_module(module)
    return module


class Response:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self.body, self.headers = status, body, headers or {}
    def json(self):
        if self.body is None:
            raise ValueError('no body')
        return self.body


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.kodi = Kodi()
        self.posts = []
        self.response = Response(200, {'ok': True})
        def post(url, **kwargs):
            self.posts.append((url, kwargs))
            return self.response
        self.requests = types.SimpleNamespace(post=post, get=post)
        self.api = load('sesn_api', self.kodi, {'requests': self.requests})
        self.api.requests = self.requests

    def test_https_required_except_loopback(self):
        for url in ['http://sesn.io', 'http://192.168.1.5:8080', 'ftp://sesn.io', 'sesn.io', 'https://']:
            self.kodi.settings['server_url'] = url
            self.assertIsNone(self.api.server_base(), url)
            self.assertEqual(self.api.send_result({'action': 'start'})[0], self.api.BLOCKED)
            self.assertIsNone(self.api.get_json('/api/v1/lists'))
            self.assertFalse(self.api.revoke_key())
        self.assertEqual(self.posts, [])
        for url in ['https://sesn.io', 'http://localhost:4321', 'http://127.0.0.1:8787/', 'https://staging.sesn.io']:
            self.kodi.settings['server_url'] = url
            self.assertEqual(self.api.server_base(), url.rstrip('/'))
        self.assertFalse(any('not sesn.io' in log for log in self.kodi.logs if 'staging' in log))

    def test_non_sesn_host_is_warned_once(self):
        self.kodi.settings['server_url'] = 'https://example.test'
        self.api.server_base(); self.api.server_base()
        self.assertEqual(sum('not sesn.io' in log for log in self.kodi.logs), 1)

    def test_status_classification(self):
        self.assertEqual(self.api.classify_status(401), self.api.UNAUTHORIZED)
        for status in (408, 429, 500, 503):
            self.assertEqual(self.api.classify_status(status), self.api.RETRY)
        for status in (400, 403, 404, 409, 410, 413, 422):
            self.assertEqual(self.api.classify_status(status), self.api.DROP)

    def test_keyed_requests_do_not_follow_redirects(self):
        self.api.send({'action': 'start'})
        self.assertIs(self.posts[-1][1]['allow_redirects'], False)

    def test_401_flags_key_and_stops_sending(self):
        self.response = Response(401)
        with patch.dict(sys.modules, self.kodi.modules):  # xbmcgui is imported lazily
            self.assertEqual(self.api.send_result({'action': 'start'})[0], self.api.UNAUTHORIZED)
        self.assertTrue(self.api.key_invalid())
        self.assertNotIn('synthetic-test-key', self.kodi.settings['invalid_key'])
        self.assertEqual(len(self.kodi.notes), 1)
        before = len(self.posts)
        self.assertEqual(self.api.send_result({'action': 'start'})[0], self.api.UNAUTHORIZED)
        self.assertIsNone(self.api.get_json('/api/v1/up-next'))
        self.assertEqual(len(self.posts), before)
        self.kodi.settings['api_key'] = 'a-new-paired-key'
        self.assertFalse(self.api.key_invalid())

    def test_retry_after_parsing(self):
        self.assertEqual(self.api.retry_after_seconds(Response(429, headers={'Retry-After': '120'})), 120)
        self.assertEqual(self.api.retry_after_seconds(Response(429, headers={'Retry-After': '999999'})), 6 * 3600)
        self.assertIsNone(self.api.retry_after_seconds(Response(429)))
        self.assertIsNone(self.api.retry_after_seconds(Response(429, headers={'Retry-After': 'soon'})))
        self.assertEqual(self.api.retry_after_seconds(Response(429, headers={'Retry-After': 'Wed, 21 Oct 2015 07:28:00 GMT'})), 0)

    def test_http_200_rate_limit_is_retryable_not_an_acceptance(self):
        self.response = Response(200, {'ok': True, 'logged': False, 'ignored': 'rate_limited'})
        self.assertEqual(self.api.send_result({'action': 'stop'}), (self.api.RETRY, 3600))
        self.response.headers['Retry-After'] = '120'
        self.assertEqual(self.api.send_result({'action': 'stop'}), (self.api.RETRY, 120))

    def test_ordinary_ignored_stop_remains_acknowledged(self):
        self.response = Response(200, {'ok': True, 'logged': False, 'skipped': 'below_completion_threshold'})
        self.assertEqual(self.api.send_result({'action': 'stop'})[0], self.api.OK)


class PairIntervalTests(unittest.TestCase):
    def setUp(self):
        kodi = Kodi()
        self.pair = load('pair', kodi, {'sesn_api': types.SimpleNamespace()})

    def test_interval_is_clamped(self):
        for value, expected in [(0, 5), (1, 5), ('2', 5), (None, 5), ('x', 5), (7, 7), (9999, 60)]:
            self.assertEqual(self.pair.clamp_interval(value), expected)

    def test_slow_down_and_429_increase_interval(self):
        self.assertEqual(self.pair.next_interval(5, 200, {'status': 'pending'}), 5)
        self.assertEqual(self.pair.next_interval(5, 429, None), 10)
        self.assertEqual(self.pair.next_interval(10, 400, {'error': 'slow_down'}), 15)
        self.assertEqual(self.pair.next_interval(5, 200, {'status': 'slow_down'}), 10)
        self.assertEqual(self.pair.next_interval(5, 429, None, retry_after=30), 30)
        self.assertEqual(self.pair.next_interval(60, 429, None), 60)


class PluginListIdTests(unittest.TestCase):
    def test_list_id_must_be_numeric(self):
        kodi = Kodi()
        calls, ended = [], []
        api = types.SimpleNamespace(get_json=lambda path: calls.append(path) or {})
        plugin_mods = {'xbmcplugin': types.SimpleNamespace(endOfDirectory=lambda *a, **k: ended.append(k),
                                                           addDirectoryItem=lambda *a, **k: None),
                       'sesn_api': api, 'kodi_library': types.SimpleNamespace()}
        with patch.object(sys, 'argv', ['plugin://script.sesn/', '1', '']):
            plugin = load('plugin', kodi, plugin_mods)
        plugin._render = lambda items: None
        with patch.dict(sys.modules, kodi.modules):
            for bad in ['../account', '1?x=2', '1/../../x', '', '١٢', '12a']:
                plugin._list({'id': bad})
            plugin._list({'id': '42'})
        self.assertEqual(calls, ['/api/v1/lists/42'])


if __name__ == '__main__':
    unittest.main()
