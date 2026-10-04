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


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.kodi = Kodi()
        self.outcomes = []
        self.sent = []
        api = types.SimpleNamespace(OK='ok', RETRY='retry', DROP='drop', UNAUTHORIZED='unauthorized', NO_KEY='no_key',
                                    BLOCKED='blocked', invalid=False, key='k1')
        def send_result(payload):
            self.sent.append(payload)
            return self.outcomes.pop(0) if self.outcomes else ('ok', None)
        api.send_result = send_result
        api.is_configured = lambda: bool(api.key)
        api.key_invalid = lambda: api.invalid
        api.key_fingerprint = lambda: api.key
        self.api = api
        self.queue = load('scrobble_queue', self.kodi, {'sesn_api': api})
        self.queue.random = types.SimpleNamespace(random=lambda: 1.0)

    def stored(self):
        return json.loads(self.kodi.files.get('/profile/queue.json') or '[]')

    def test_success_sends_without_queueing(self):
        self.assertTrue(self.queue.enqueue({'action': 'start'}, now=1000))
        self.assertEqual(self.stored(), [])

    def test_4xx_drops_event(self):
        self.outcomes = [('drop', None)]
        self.assertFalse(self.queue.enqueue({'action': 'stop'}, now=1000))
        self.assertEqual(self.stored(), [])

    def test_retry_queues_backs_off_and_honours_retry_after(self):
        self.outcomes = [('retry', 300)]
        self.assertFalse(self.queue.enqueue({'action': 'stop'}, now=1000))
        self.assertEqual(len(self.stored()), 1)
        self.assertEqual(self.queue._next_attempt, 1300)
        self.queue.flush(now=1200)  # still backing off: nothing sent
        self.assertEqual(len(self.sent), 1)
        self.assertFalse(self.queue.enqueue({'action': 'start'}, now=1200))  # queued behind, unsent
        self.assertEqual(len(self.sent), 1)
        self.queue.flush(now=1301)
        self.assertEqual(len(self.sent), 3)
        self.assertEqual(self.stored(), [])
        self.assertEqual(self.queue._failures, 0)

    def test_backoff_grows_with_cap(self):
        delays = [self.queue.backoff_delay(n, rand=lambda: 1.0) for n in range(1, 12)]
        self.assertEqual(delays[:4], [10, 20, 40, 80])
        self.assertEqual(delays[-1], 30 * 60)
        self.assertEqual(self.queue.backoff_delay(1, rand=lambda: 0.0), 5)
        self.assertEqual(self.queue.backoff_delay(1, retry_after=600, rand=lambda: 1.0), 600)

    def test_unauthorized_stops_until_new_key(self):
        self.outcomes = [('unauthorized', None)]
        self.queue.enqueue({'action': 'start'}, now=1000)
        self.api.invalid = True
        self.queue.flush(now=5000)
        self.assertEqual(len(self.sent), 1)
        self.api.invalid, self.api.key = False, 'k2'  # paired again
        self.queue.flush(now=1001)  # new key: old backoff no longer applies
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.stored(), [])

    def test_flush_drops_4xx_and_stops_at_retry(self):
        self.kodi.files['/profile/queue.json'] = json.dumps([
            {'payload': {'n': 1}, 'queued_at': 1000}, {'payload': {'n': 2}, 'queued_at': 1000},
            {'payload': {'n': 3}, 'queued_at': 1000}])
        self.outcomes = [('drop', None), ('retry', None)]
        self.queue.flush(now=2000)
        self.assertEqual([p['n'] for p in self.sent], [1, 2])
        self.assertEqual([i['payload']['n'] for i in self.stored()], [2, 3])

    def test_old_events_expire_and_legacy_format_is_read(self):
        week = 7 * 24 * 3600
        self.kodi.files['/profile/queue.json'] = json.dumps([
            {'payload': {'n': 'old'}, 'queued_at': 1000}, {'payload': {'n': 'new'}, 'queued_at': 1000 + week}])
        self.queue.flush(now=1001 + week)
        self.assertEqual([p['n'] for p in self.sent], ['new'])
        self.kodi.files['/profile/queue.json'] = json.dumps([{'action': 'stop', 'title': 'legacy'}])
        self.queue.flush(now=5000)
        self.assertEqual(self.sent[-1], {'action': 'stop', 'title': 'legacy'})


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


class SyncLoopTests(unittest.TestCase):
    def setUp(self):
        self.kodi = Kodi()
        self.kodi.settings['sync_down'] = True
        self.pages = []
        self.requests = []
        def get_json(path):
            self.requests.append(path)
            return self.pages.pop(0) if self.pages else None
        library = types.SimpleNamespace(movie_index=lambda: {}, show_index=lambda: {}, find_movie=lambda *a, **k: None,
                                        find_episode=lambda *a, **k: None, rpc=lambda *a: {})
        self.sync = load('sync', self.kodi, {'sesn_api': types.SimpleNamespace(get_json=get_json), 'kodi_library': library})

    def test_pauses_between_pages_and_stops_on_stuck_cursor(self):
        self.pages = [{'ok': True, 'items': [], 'cursor': 10, 'has_more': True},
                      {'ok': True, 'items': [], 'cursor': 10, 'has_more': True},
                      {'ok': True, 'items': [], 'cursor': 20, 'has_more': False}]
        with patch.dict(sys.modules, self.kodi.modules):
            self.sync.run(full=True)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.kodi.waits, [1])
        self.assertIn('since=10', self.requests[1])


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
