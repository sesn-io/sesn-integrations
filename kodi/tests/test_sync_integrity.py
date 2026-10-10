"""Offline JSON-RPC failure and checkpoint regression tests."""
import json
import unittest
import tempfile
import os
import types
from unittest.mock import patch

from test_security import Kodi, load


class SyncIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.kodi = Kodi()
        self.kodi.settings['sync_down'] = True
        self.calls = []
        self.replies = {
            'VideoLibrary.GetMovies': {'movies': [{'movieid': 1, 'uniqueid': {'tmdb': '42'}}], 'limits': {'start': 0, 'end': 1, 'total': 1}},
            'VideoLibrary.GetTVShows': {'tvshows': [], 'limits': {'start': 0, 'end': 0, 'total': 0}},
            'VideoLibrary.GetMovieDetails': {'moviedetails': {'playcount': 0}},
            'VideoLibrary.SetMovieDetails': 'OK',
        }
        self.fail = None
        def execute(raw):
            request = json.loads(raw)
            self.calls.append(request)
            if request['method'] == self.fail:
                return json.dumps({'error': {'code': -32603, 'message': 'fixture'}})
            return json.dumps({'result': self.replies[request['method']]})
        self.kodi.modules['xbmc'].executeJSONRPC = execute
        self.library = load('kodi_library', self.kodi)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = load('local_state', self.kodi)
        self.store.PROFILE = self.tmp.name
        self.store.DATABASE = os.path.join(self.tmp.name, 'state.sqlite')
        self.owner = 'owner-a'
        self.account = types.SimpleNamespace(current=lambda: self.owner)
        self.api = types.SimpleNamespace(transport_fingerprint=lambda: self.owner, get_json=lambda path, **kw: {
            'ok': True, 'items': [{'type': 'movie', 'tmdb_id': 42, 'watched_at': 1000}],
            'cursor': 20, 'has_more': False})
        self.sync = load('sync', self.kodi, {'sesn_api': self.api, 'kodi_library': self.library, 'account': self.account, 'local_state': self.store})

    def run_sync(self, full=False):
        with patch.dict('sys.modules', self.kodi.modules):
            return self.sync.run(full=full)

    def test_failed_read_never_writes_or_checkpoints(self):
        self.fail = 'VideoLibrary.GetMovieDetails'
        self.assertFalse(self.run_sync())
        self.assertNotIn('VideoLibrary.SetMovieDetails', [r['method'] for r in self.calls])
        self.assertEqual(self.sync._read_cursor(), 0)

    def test_setter_failure_retries_page_after_recovery(self):
        self.fail = 'VideoLibrary.SetMovieDetails'
        self.assertFalse(self.run_sync())
        self.assertEqual(self.sync._read_cursor(), 0)
        self.fail = None
        self.assertTrue(self.run_sync())
        self.assertEqual(self.sync._read_cursor(), 20)

    def test_existing_playcount_four_is_never_replaced(self):
        self.replies['VideoLibrary.GetMovieDetails'] = {'moviedetails': {'playcount': 4}}
        self.assertTrue(self.run_sync())
        self.assertNotIn('VideoLibrary.SetMovieDetails', [r['method'] for r in self.calls])
        self.assertEqual(self.sync._read_cursor(), 20)

    def test_incomplete_read_or_write_cannot_be_acknowledged(self):
        for value in ({}, {'moviedetails': {}}, {'moviedetails': {'playcount': '4'}}):
            self.replies['VideoLibrary.GetMovieDetails'] = value
            self.assertFalse(self.run_sync())
            self.assertEqual(self.sync._read_cursor(), 0)
        self.replies['VideoLibrary.GetMovieDetails'] = {'moviedetails': {'playcount': 0}}
        self.replies['VideoLibrary.SetMovieDetails'] = {}
        self.assertFalse(self.run_sync())
        self.assertEqual(self.sync._read_cursor(), 0)

    def test_failed_library_index_is_not_an_empty_library(self):
        self.fail = 'VideoLibrary.GetMovies'
        self.assertFalse(self.run_sync())
        self.assertEqual(self.sync._read_cursor(), 0)

    def test_incomplete_full_reconcile_preserves_previous_checkpoint(self):
        self.sync._write_cursor(7)
        self.fail = 'VideoLibrary.SetMovieDetails'
        self.assertFalse(self.run_sync(full=True))
        self.assertEqual(self.sync._read_cursor(), 7)

    def test_protocol_error_is_not_swallowed_by_checked_rpc(self):
        self.fail = 'VideoLibrary.GetMovies'
        with self.assertRaises(self.library.RpcError):
            self.library.rpc_checked(self.fail)
        self.assertEqual(self.library.rpc(self.fail), {})

    def test_daily_restarts_still_trigger_weekly_reconciliation(self):
        with patch.object(self.sync.time, 'time', return_value=1000):
            self.assertTrue(self.run_sync(full=True))
        for day in range(1, 9):
            restarted = load('sync', self.kodi, {'sesn_api': self.api, 'kodi_library': self.library,
                                                'account': self.account, 'local_state': self.store})
            self.assertEqual(restarted.full_due(now=1000 + day * 86400), day >= 7)

    def test_account_switch_has_its_own_cursor_and_reconcile_schedule(self):
        self.sync._write_cursor(100)
        self.owner = 'owner-b'
        self.assertEqual(self.sync._read_cursor(), 0)
        self.assertTrue(self.sync.full_due())
        self.owner = 'owner-a'
        self.assertEqual(self.sync._read_cursor(), 100)

    def test_interrupted_full_reconcile_resumes_durable_page_checkpoint(self):
        self.sync.MAX_PAGES = 1
        self.api.get_json = lambda path, **kw: {'ok': True, 'items': [], 'cursor': 20, 'has_more': True}
        self.assertFalse(self.run_sync(full=True))
        self.assertEqual(self.sync._state(self.owner), (0, 20, 0))
        self.assertTrue(self.sync.full_due())
        paths = []
        self.api.get_json = lambda path, **kw: paths.append(path) or {'ok': True, 'items': [], 'cursor': 30, 'has_more': False}
        self.assertTrue(self.run_sync(full=True))
        self.assertIn('since=20', paths[0])
        self.assertEqual(self.sync._read_cursor(), 30)

    def test_library_scan_makes_previously_missing_title_reconcile(self):
        self.replies['VideoLibrary.GetMovies'] = {'movies': [], 'limits': {'start': 0, 'end': 0, 'total': 0}}
        self.assertTrue(self.run_sync(full=True))
        self.assertFalse(self.sync.full_due())
        self.replies['VideoLibrary.GetMovies'] = {'movies': [{'movieid': 1, 'uniqueid': {'tmdb': '42'}}], 'limits': {'start': 0, 'end': 1, 'total': 1}}
        self.sync.invalidate_library()
        self.assertTrue(self.sync.full_due())
        self.assertTrue(self.run_sync(full=True))
        self.assertIn('VideoLibrary.SetMovieDetails', [r['method'] for r in self.calls])

    def test_stuck_cursor_pauses_without_claiming_full_completion(self):
        self.api.get_json = lambda path, **kw: {'ok': True, 'items': [], 'cursor': 0, 'has_more': True}
        self.assertFalse(self.run_sync(full=True))
        self.assertEqual(self.sync._state(self.owner)[2], 0)

    def test_truncated_library_never_looks_like_absent_title(self):
        self.replies['VideoLibrary.GetMovies'] = {'movies': [], 'limits': {'start': 0, 'end': 0, 'total': 1}}
        self.assertFalse(self.run_sync(full=True))
        self.assertEqual(self.sync._state(self.owner), (0, None, 0))
        self.assertNotIn('VideoLibrary.SetMovieDetails', [r['method'] for r in self.calls])

    def test_missing_invalid_or_boolean_library_ids_cannot_be_checkpointed(self):
        for local_id in (None, 0, -1, True, '1'):
            row = {'uniqueid': {'tmdb': '42'}}
            if local_id is not None:
                row['movieid'] = local_id
            self.replies['VideoLibrary.GetMovies'] = {'movies': [row], 'limits': {'start': 0, 'end': 1, 'total': 1}}
            self.assertFalse(self.run_sync(full=True))
            self.assertEqual(self.sync._state(self.owner), (0, None, 0))

    def test_missing_or_inconsistent_library_limits_fail_closed(self):
        row = {'movieid': 1, 'uniqueid': {'tmdb': '42'}}
        for limits in (None, {}, {'total': 1}, {'start': True, 'end': 1, 'total': 1},
                       {'start': 1, 'end': 2, 'total': 2}, {'start': 0, 'end': 2, 'total': 2}):
            self.replies['VideoLibrary.GetMovies'] = {'movies': [row], 'limits': limits}
            self.assertFalse(self.run_sync())
            self.assertEqual(self.sync._read_cursor(), 0)

    def test_ids_can_be_absent_when_library_row_is_otherwise_complete(self):
        self.replies['VideoLibrary.GetMovies'] = {'movies': [{'movieid': 1}], 'limits': {'start': 0, 'end': 1, 'total': 1}}
        self.assertTrue(self.run_sync())
        self.assertEqual(self.sync._read_cursor(), 20)
        self.assertNotIn('VideoLibrary.SetMovieDetails', [r['method'] for r in self.calls])

    def test_owner_switch_inside_detail_read_never_reaches_setter(self):
        original = self.library.rpc_checked
        def checked(method, params):
            result = original(method, params)
            if method == 'VideoLibrary.GetMovieDetails':
                self.owner = 'owner-b'
            return result
        self.library.rpc_checked = checked
        self.assertFalse(self.run_sync())
        self.assertNotIn('VideoLibrary.SetMovieDetails', [r['method'] for r in self.calls])
        self.assertEqual(self.sync._state('owner-a'), (0, None, 0))
        self.assertEqual(self.sync._state('owner-b'), (0, None, 0))

    def test_owner_switch_after_setter_prevents_acknowledgement_and_full_completion(self):
        original = self.library.rpc_checked
        def checked(method, params):
            result = original(method, params)
            if method == 'VideoLibrary.SetMovieDetails':
                self.owner = 'owner-b'
            return result
        self.library.rpc_checked = checked
        self.assertFalse(self.run_sync(full=True))
        self.assertIn('VideoLibrary.SetMovieDetails', [r['method'] for r in self.calls])
        self.assertEqual(self.sync._state('owner-a'), (0, None, 0))
        self.assertEqual(self.sync._state('owner-b'), (0, None, 0))

    def test_owner_switch_at_checkpoint_boundary_cannot_save_it(self):
        original = self.sync._write_cursor
        def checkpoint(cursor, owner=None, transport=None):
            self.owner = 'owner-b'
            return original(cursor, owner, transport)
        self.sync._write_cursor = checkpoint
        self.assertFalse(self.run_sync())
        self.assertEqual(self.sync._state('owner-a'), (0, None, 0))

    def test_episode_and_show_rows_require_local_ids(self):
        self.replies['VideoLibrary.GetTVShows'] = {'tvshows': [{'uniqueid': {'tmdb': '42'}}],
                                                  'limits': {'start': 0, 'end': 1, 'total': 1}}
        self.assertFalse(self.run_sync())
        self.replies['VideoLibrary.GetEpisodes'] = {'episodes': [{'episode': 2}],
                                                   'limits': {'start': 0, 'end': 1, 'total': 1}}
        with self.assertRaises(self.library.RpcError):
            self.library.find_episode({'tmdb': 42}, 1, 2, index={'tmdb:42': 1}, strict=True)


if __name__ == '__main__':
    unittest.main()
