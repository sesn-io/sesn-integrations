"""Offline tests for accepted versus merely HTTP-successful scrobbles."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


class ApiAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.logs = []
        self.body = {'ok': True, 'queued': True}
        self.status = 200
        self.settings = {'api_key': 'synthetic-test-key', 'server_url': 'https://fixture.invalid'}
        addon = types.SimpleNamespace(getSettingString=lambda k: self.settings.get(k, ''), getSettingBool=lambda k: True)
        def post(url, **kwargs):
            self.requests.append((url, kwargs))
            def json():
                if isinstance(self.body, Exception):
                    raise self.body
                return self.body
            return types.SimpleNamespace(status_code=self.status, json=json)
        mocks = {'xbmc': types.SimpleNamespace(log=lambda msg, level: self.logs.append(msg), LOGINFO=1, LOGWARNING=2, LOGERROR=3),
                 'xbmcaddon': types.SimpleNamespace(Addon=lambda: addon), 'requests': types.SimpleNamespace(post=post)}
        spec = importlib.util.spec_from_file_location('sesn_api_under_test', Path(__file__).resolve().parents[1] / 'script.sesn/resources/lib/sesn_api.py')
        self.api = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, mocks):
            spec.loader.exec_module(self.api)

    def test_accepted_queue_and_saved_events(self):
        for body in [{'ok': True, 'queued': True}, {'ok': True, 'logged': True}, {'ok': True, 'logged': False, 'action': 'start'}]:
            self.body = body
            self.assertTrue(self.api.send({'type': 'movie', 'action': 'stop', 'progress': 100}))
        url, options = self.requests[0]
        self.assertEqual(url, 'https://fixture.invalid/api/v1/scrobble')
        self.assertEqual(options['headers']['X-Api-Key'], 'synthetic-test-key')
        self.assertNotIn('synthetic-test-key', str(self.logs))

    def test_http_success_without_positive_api_ack_is_not_success(self):
        for body in [None, [], {}, {'ok': False}, {'ok': 'true'}, ValueError('bad JSON')]:
            self.body = body
            self.assertFalse(self.api.send({'type': 'movie'}))

    def test_failure_and_unpaired_state(self):
        for status in [401, 403, 429, 503]:
            self.status = status
            self.assertFalse(self.api.send({'type': 'movie'}))
        self.settings['api_key'] = ''
        before = len(self.requests)
        self.assertFalse(self.api.send({'type': 'movie'}))
        self.assertEqual(len(self.requests), before)


if __name__ == '__main__':
    unittest.main()
