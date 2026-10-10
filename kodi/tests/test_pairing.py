"""Exercise the maintained pairing client's provider-scoped request offline."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


class PairingTests(unittest.TestCase):
    def setUp(self):
        self.calls, self.saved, self.notifications = [], {}, []
        self.cancelled = False
        self.start_ok = True
        self.polls = [(200, {"status": "authorized", "api_key": "synthetic-kodi-key"}, None)]

        def start(path, body):
            self.calls.append((path, body))
            # Model the provider-required endpoint. An unscoped request must
            # never reach the successful pairing path in this fixture.
            if not self.start_ok or body != {"provider": "kodi", "device_name": "Kodi"}:
                return {"ok": False, "error": "provider_required"}
            return {"ok": True, "device_code": "synthetic-device-code",
                    "user_code_display": "FIXTURE", "verification_url": "https://fixture.invalid/link",
                    "interval": 5, "expires_in": 900}

        def poll(path, body):
            self.calls.append((path, body))
            return self.polls.pop(0)

        addon = types.SimpleNamespace(setSettingString=lambda key, value: self.saved.__setitem__(key, value))
        dialog = types.SimpleNamespace(create=lambda *args: None, update=lambda *args: None,
                                       close=lambda: None, iscanceled=lambda: self.cancelled)
        modules = {
            "xbmc": types.SimpleNamespace(sleep=lambda milliseconds: None),
            "xbmcaddon": types.SimpleNamespace(Addon=lambda: addon),
            "xbmcgui": types.SimpleNamespace(DialogProgress=lambda: dialog,
                Dialog=lambda: types.SimpleNamespace(notification=lambda *args: self.notifications.append(args)),
                NOTIFICATION_ERROR=1, NOTIFICATION_INFO=2, NOTIFICATION_WARNING=3),
            "sesn_api": types.SimpleNamespace(post_json=start, post_json_ex=poll),
        }
        source = Path(__file__).resolve().parents[1] / "script.sesn/resources/lib/pair.py"
        spec = importlib.util.spec_from_file_location("pairing_under_test", source)
        self.pair = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, modules):
            spec.loader.exec_module(self.pair)

    def test_pairing_declares_kodi_and_saves_only_the_authorized_key(self):
        self.pair.run()
        self.assertEqual(self.calls, [
            ("/api/v1/link/new", {"provider": "kodi", "device_name": "Kodi"}),
            ("/api/v1/link/poll", {"device_code": "synthetic-device-code"}),
        ])
        self.assertEqual(self.saved, {"api_key": "synthetic-kodi-key"})
        self.assertNotIn("synthetic-kodi-key", str(self.notifications))

    def test_failed_start_never_polls_or_saves_a_key(self):
        self.start_ok = False
        self.pair.run()
        self.assertEqual(self.calls, [("/api/v1/link/new", {"provider": "kodi", "device_name": "Kodi"})])
        self.assertEqual(self.saved, {})

    def test_http_error_cannot_authorize_a_key(self):
        self.polls = [(403, {"status": "authorized", "api_key": "synthetic-rejected-key"}, None),
                      (200, {"status": "expired"}, None)]
        self.pair.run()
        self.assertEqual(self.saved, {})
        self.assertEqual(len(self.calls), 3)
        self.assertNotIn("synthetic-rejected-key", str(self.notifications))

    def test_cancelled_pairing_never_polls_or_saves_a_key(self):
        self.cancelled = True
        self.pair.run()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.saved, {})


if __name__ == "__main__":
    unittest.main()
