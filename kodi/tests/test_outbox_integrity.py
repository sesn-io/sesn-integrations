"""Real temporary SQLite, synthetic accounts, no Kodi or HTTP service required."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import subprocess
import sys
import unittest
from unittest.mock import patch

from test_security import Kodi, load, Response


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.kodi = Kodi()
        self.store = load('local_state', self.kodi)
        self.store.PROFILE = self.tmp.name
        self.store.DATABASE = os.path.join(self.tmp.name, 'state.sqlite')
        self.api = load('sesn_api', self.kodi)
        self.account_id = '42'
        self.posts = []
        self.result = None
        import types
        def post(url, **kw):
            body = json.loads(kw['data'])
            self.posts.append((url, kw, body))
            if url.endswith('/register'):
                return Response(200, {'ok': True, 'account_id': self.account_id})
            if self.result:
                return self.result(body) if callable(self.result) else self.result
            return Response(200, {'ok': True, 'protocol_version': 1, 'durable': True,
                                  'event_id': body['event_id'], 'state': 'applied'})
        self.api.requests = types.SimpleNamespace(post=post)
        self.account = load('account', self.kodi, {'sesn_api': self.api, 'local_state': self.store})
        self.mods = {'sesn_api': self.api, 'local_state': self.store, 'account': self.account}
        self.queue = load('scrobble_queue', self.kodi, self.mods)
        self.owner = self.account.verify_current()
        self.posts.clear()

    def rows(self):
        with self.store.database() as db:
            return db.execute('SELECT owner,payload,queued_at FROM outbox ORDER BY id').fetchall()

    def watches(self):
        return [p for p in self.posts if p[0].endswith('/events')]

    def test_capture_commits_before_any_network_and_survives_reload(self):
        event = {'action': 'stop', 'progress': 100, 'title': 'fixture'}
        self.assertTrue(self.queue.capture(event, now=1000))
        event['title'] = 'changed after capture'
        self.assertEqual(self.posts, [])
        restarted = load('scrobble_queue', self.kodi, self.mods)
        self.assertEqual(restarted.flush(now=999999999), 1)
        delivered = self.watches()[0][2]
        self.assertEqual(delivered['title'], 'fixture')
        self.assertEqual(delivered['watched_at'], 1000)
        self.assertEqual(delivered['provider'], 'kodi')
        self.assertEqual(self.rows(), [])

    def test_account_switch_never_replays_old_owner_and_unpaired_never_captures(self):
        self.queue.capture({'title': 'A', 'action': 'stop'}, now=1000)
        self.kodi.settings['api_key'] = ''
        self.assertFalse(self.queue.capture({'title': 'unpaired'}))
        self.assertEqual(self.queue.flush(), 0)
        self.kodi.settings['api_key'] = 'account-b-key'
        self.account_id = '84'
        self.assertIsNone(self.account.current())
        self.assertFalse(self.queue.capture({'title': 'unverified'}))
        self.account.verify_current()
        self.queue.capture({'title': 'B', 'action': 'stop'}, now=2000)
        self.queue.flush(now=3000)
        self.assertEqual([w[2]['title'] for w in self.watches()], ['B'])
        self.assertEqual(json.loads(self.rows()[0][1])['title'], 'A')

    def test_verified_token_rotation_resumes_same_account_only(self):
        self.queue.capture({'title': 'A', 'action': 'stop'}, now=1000)
        self.kodi.settings['api_key'] = 'rotated-key'
        self.assertEqual(self.queue.flush(), 0)
        self.assertEqual(self.account.verify_current(), self.owner)
        self.assertEqual(self.queue.flush(now=2000), 1)
        self.assertEqual(self.watches()[0][2]['provider'], 'kodi')

    def test_server_change_and_legacy_queue_are_quarantined(self):
        self.queue.capture({'title': 'old server'})
        legacy = Path(self.tmp.name, 'queue.json')
        old = '[{"payload":{"title":"unbound"},"queued_at":10}]'
        legacy.write_text(old)
        self.kodi.settings['server_url'] = 'https://other.invalid'
        self.assertNotEqual(self.account.verify_current(), self.owner)
        self.queue.warn_legacy_queue()
        self.assertEqual(self.queue.flush(), 0)
        self.assertEqual(legacy.read_text(), old)
        self.assertEqual(len(self.rows()), 1)

    def test_lost_ack_and_rate_limit_preserve_exact_event_and_timestamp(self):
        self.queue.capture({'title': 'New Year', 'watched_at': 1767225599}, now=1767225599)
        self.result = Response(200, {'ok': True, 'ignored': 'rate_limited', 'logged': False})
        self.assertEqual(self.queue.flush(now=1767225600), 0)
        self.assertEqual(self.queue._next_attempt, 1767229200)
        first = self.watches()[0][2]
        self.result = None
        self.assertEqual(self.queue.flush(now=1767398400), 1)
        self.assertEqual(self.watches()[-1][2], first)
        self.assertEqual(first['watched_at'], 1767225599)

    def test_unsupported_or_legacy_ack_is_never_durable(self):
        self.queue.capture({'title': 'retained'})
        for response in [Response(404), Response(200, {'ok': True, 'queued': True}),
            Response(200, {'ok': True, 'durable': True, 'protocol_version': 1, 'event_id': 'wrong', 'state': 'applied'})]:
            self.queue._note_success()
            self.result = response
            self.assertEqual(self.queue.flush(), 0)
            self.assertEqual(len(self.rows()), 1)
        self.assertTrue(all(p[0].endswith('/events') for p in self.posts))

    def test_full_queue_preserves_every_existing_row(self):
        self.queue.MAX_QUEUED = 2
        self.queue.capture({'title': 'one'})
        self.queue.capture({'title': 'two'})
        self.assertFalse(self.queue.capture({'title': 'three'}))
        self.assertEqual([json.loads(r[1])['title'] for r in self.rows()], ['one', 'two'])
        self.assertTrue(any('full' in log for log in self.kodi.logs))

    def test_corrupt_database_is_never_replaced(self):
        Path(self.store.DATABASE).write_bytes(b'corrupt fixture')
        self.assertFalse(self.queue.capture({'title': 'not saved'}))
        self.assertEqual(self.queue.flush(), 0)
        self.assertEqual(Path(self.store.DATABASE).read_bytes(), b'corrupt fixture')
        self.assertTrue(self.account.storage_failed())
        self.assertTrue(any('storage is unavailable' in log for log in self.kodi.logs))
        self.assertEqual(len(self.kodi.notes), 1)

    def test_callback_during_post_is_not_lost_by_acknowledgement(self):
        self.queue.capture({'title': 'first'})
        def response(body):
            self.queue.capture({'title': 'captured during request'})
            return Response(200, {'ok': True, 'protocol_version': 1, 'durable': True, 'event_id': body['event_id'], 'state': 'applied'})
        self.result = response
        self.assertEqual(self.queue.flush(), 1)
        self.assertEqual([json.loads(r[1])['title'] for r in self.rows()], ['captured during request'])

    def test_switch_during_post_stops_remaining_old_owner_rows(self):
        self.queue.capture({'title': 'one'})
        self.queue.capture({'title': 'two'})
        def response(body):
            self.kodi.settings['api_key'] = 'new unverified key'
            return Response(200, {'ok': True, 'protocol_version': 1, 'durable': True, 'event_id': body['event_id'], 'state': 'applied'})
        self.result = response
        self.assertEqual(self.queue.flush(), 1)
        self.assertEqual(len(self.watches()), 1)
        self.assertEqual(json.loads(self.rows()[0][1])['title'], 'two')

    def test_rolled_back_transaction_keeps_prior_capture(self):
        self.queue.capture({'title': 'safe'})
        with self.assertRaises(RuntimeError):
            with self.store.database() as db:
                db.execute('DELETE FROM outbox')
                raise RuntimeError('injected process failure before commit')
        self.assertEqual(len(self.rows()), 1)

    def test_no_raw_key_is_persisted(self):
        self.queue.capture({'title': 'safe'})
        self.assertNotIn(b'synthetic-test-key', Path(self.store.DATABASE).read_bytes())

    def test_backoff_is_bounded_and_honours_retry_after(self):
        self.assertEqual(self.queue.backoff_delay(1, rand=lambda: 1), 10)
        self.assertEqual(self.queue.backoff_delay(100000, rand=lambda: 1), 1800)
        self.assertEqual(self.queue.backoff_delay(1, 3600), 3600)

    def test_process_killed_mid_ack_transaction_recovers_original_row(self):
        self.queue.capture({'title': 'safe across crash'})
        child = subprocess.run([sys.executable, '-c',
            'import sqlite3,sys,os; db=sqlite3.connect(sys.argv[1]); '
            'db.execute("PRAGMA synchronous=FULL"); db.execute("BEGIN IMMEDIATE"); '
            'db.execute("DELETE FROM outbox"); os._exit(17)', self.store.DATABASE], check=False)
        self.assertEqual(child.returncode, 17)
        self.assertEqual(json.loads(self.rows()[0][1])['title'], 'safe across crash')

    def test_stalled_network_does_not_block_capture_on_another_thread(self):
        self.queue.capture({'title': 'first'})
        entered, release = threading.Event(), threading.Event()
        def response(body):
            entered.set()
            release.wait(5)
            return Response(503)
        self.result = response
        worker = threading.Thread(target=self.queue.flush)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertTrue(self.queue.capture({'title': 'during blocked network'}))
            self.assertEqual(len(self.rows()), 2)
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())

    def test_acknowledgement_storage_failure_replays_same_event(self):
        self.queue.capture({'title': 'committed remotely'})
        old_database = self.store.database
        def response(body):
            self.store.database = lambda: (_ for _ in ()).throw(OSError('ack failure'))
            return Response(200, {'ok': True, 'protocol_version': 1, 'durable': True, 'event_id': body['event_id'], 'state': 'applied'})
        self.result = response
        try:
            self.assertEqual(self.queue.flush(), 0)
        finally:
            self.store.database = old_database
        self.result = None
        self.assertEqual(self.queue.flush(), 1)
        self.assertEqual(self.watches()[0][2], self.watches()[1][2])

    def test_401_for_old_key_does_not_reject_new_key(self):
        self.queue.capture({'title': 'A'})
        def response(body):
            self.kodi.settings['api_key'] = 'replacement-key'
            return Response(401)
        self.result = response
        self.queue.flush()
        self.assertFalse(self.api.key_invalid())


if __name__ == '__main__':
    unittest.main()
