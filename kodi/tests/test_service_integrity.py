"""Scheduling tests use synthetic monitors, never start Kodi or contact HTTP."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from test_security import Kodi


class ServiceIntegrityTests(unittest.TestCase):
    def load_service(self, extra):
        kodi = Kodi()
        spec = importlib.util.spec_from_file_location('service_fixture', Path(__file__).resolve().parents[1] / 'script.sesn/service.py')
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {**kodi.modules, **extra}):
            spec.loader.exec_module(module)
        return module

    def test_player_sampling_runs_without_network_drain_on_main_thread(self):
        calls = []
        class Mon:
            def abortRequested(self): return calls.count('sample') >= 3
            def waitForAbort(self, seconds): calls.append(('sample-wait', seconds)); return False
        player = types.SimpleNamespace(sample_progress=lambda: calls.append('sample'))
        queue = types.SimpleNamespace(warn_legacy_queue=lambda: None)
        module = self.load_service({'monitor': types.SimpleNamespace(SesnPlayer=lambda: player),
            'account': types.SimpleNamespace(), 'scrobble_queue': queue, 'sync': types.SimpleNamespace(), 'sesn_api': types.SimpleNamespace()})
        module.LibraryMonitor = Mon
        thread = types.SimpleNamespace(start=lambda: calls.append('worker-start'), join=lambda **kw: calls.append('worker-join'))
        with patch.object(module.threading, 'Thread', return_value=thread) as create:
            module.run()
        self.assertEqual(calls.count('sample'), 3)
        self.assertEqual([c for c in calls if isinstance(c, tuple)], [('sample-wait', 1)] * 3)
        self.assertEqual(create.call_args.kwargs['target'], module.network_loop)

    def test_only_scan_notifications_schedule_reconcile(self):
        module = self.load_service({name: types.SimpleNamespace() for name in ('monitor', 'account', 'scrobble_queue', 'sync', 'sesn_api')})
        mon = module.LibraryMonitor()
        mon.onNotification('kodi', 'VideoLibrary.OnUpdate', '{}')
        self.assertFalse(mon.library_changed.is_set())
        mon.onNotification('kodi', 'VideoLibrary.OnScanFinished', '{}')
        self.assertTrue(mon.library_changed.is_set())


if __name__ == '__main__':
    unittest.main()
