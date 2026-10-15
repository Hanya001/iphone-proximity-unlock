import json
import unittest
from unittest.mock import patch

import watcher
from config import DEFAULTS


ADDRESS = "AA:BB:CC:DD:EE:FF"


class UnlockTests(unittest.TestCase):
    def check_request(self, strength, expected):
        with patch.object(watcher, "desktop_sessions", return_value=[("16", True)]), \
                patch.object(watcher, "phone_state", return_value=True), \
                patch.object(watcher, "signal_strength", return_value=strength) as signal, \
                patch.object(watcher, "command", return_value="") as execute:
            self.assertEqual(watcher.request_unlock(ADDRESS, -45), expected)
            signal.assert_called_once_with(ADDRESS)
            self.assertEqual(execute.call_count, int(expected))

    def test_single_sample_at_threshold(self):
        self.check_request(-45, True)

    def test_weak_signal(self):
        self.check_request(-46, False)

    def test_missing_signal(self):
        self.check_request(None, False)

    def test_unpaired_device_cannot_connect_or_unlock(self):
        with patch.object(watcher, "desktop_sessions", return_value=[("16", True)]), \
                patch.object(watcher, "phone_state", return_value=None), \
                patch.object(watcher, "command") as execute:
            self.assertFalse(watcher.request_unlock(ADDRESS, -45))
            execute.assert_not_called()

    def test_changed_session_is_not_unlocked(self):
        with patch.object(watcher, "desktop_sessions", side_effect=[[("16", True)], [("17", True)]]), \
                patch.object(watcher, "phone_state", return_value=True), \
                patch.object(watcher, "signal_strength", return_value=-30), \
                patch.object(watcher, "command") as execute:
            self.assertFalse(watcher.request_unlock(ADDRESS, -45))
            execute.assert_not_called()

    def test_saved_disabled_setting_blocks_request(self):
        with patch("sys.argv", ["watcher.py", "--request"]), \
                patch.object(watcher, "load_config", return_value=DEFAULTS.copy()), \
                patch.object(watcher, "request_unlock") as unlock, self.assertRaises(SystemExit) as result:
            watcher.main()
        self.assertEqual(result.exception.code, 1)
        unlock.assert_not_called()

    def test_request_uses_saved_device_and_threshold(self):
        settings = {**DEFAULTS, "enabled": True, "device_address": ADDRESS, "near_threshold": -50}
        with patch("sys.argv", ["watcher.py", "--request", "--near", "-45"]), \
                patch.object(watcher, "load_config", return_value=settings), \
                patch.object(watcher, "request_unlock", return_value=True) as unlock, self.assertRaises(SystemExit):
            watcher.main()
        unlock.assert_called_once_with(ADDRESS, -50, auto_connect=False)

    def test_disconnected_phone_does_not_trigger_connection_by_default(self):
        with patch.object(watcher, "desktop_sessions", return_value=[("16", True)]), \
                patch.object(watcher, "phone_state", return_value=False), \
                patch.object(watcher, "signal_strength") as signal, \
                patch.object(watcher, "command") as execute:
            self.assertFalse(watcher.request_unlock(ADDRESS, -45))
            execute.assert_not_called()
            signal.assert_not_called()

    def test_explicit_auto_connect_can_reconnect(self):
        with patch.object(watcher, "desktop_sessions", return_value=[("16", True)]), \
                patch.object(watcher, "phone_state", side_effect=[False, True]), \
                patch.object(watcher, "signal_strength", return_value=-30), \
                patch.object(watcher.socket, "socket") as socket_factory, \
                patch.object(watcher, "command", return_value="") as execute:
            channel = socket_factory.return_value.__enter__.return_value
            channel.getsockopt.return_value = bytes([3, 16])
            self.assertTrue(watcher.request_unlock(ADDRESS, -45, auto_connect=True))
            channel.connect.assert_called_once_with((ADDRESS, watcher.SDP_PSM))
            channel.setsockopt.assert_called_once_with(watcher.socket.SOL_BLUETOOTH, watcher.BT_SECURITY, bytes([3, 0]))
            self.assertEqual(execute.call_args_list[0].args[0], ["/usr/bin/loginctl", "unlock-session", "16"])
            self.assertEqual(execute.call_count, 1)
            socket_factory.return_value.__exit__.assert_called_once()

    def test_failed_explicit_reconnection_does_not_unlock(self):
        with patch.object(watcher, "desktop_sessions", return_value=[("16", True)]), \
                patch.object(watcher, "phone_state", return_value=False), \
                patch.object(watcher, "signal_strength") as signal, \
                patch.object(watcher.socket, "socket") as socket_factory, \
                patch.object(watcher, "command", return_value=None) as execute:
            socket_factory.return_value.__enter__.return_value.connect.side_effect = TimeoutError("timeout")
            self.assertFalse(watcher.request_unlock(ADDRESS, -45, auto_connect=True))
            execute.assert_not_called()
            signal.assert_not_called()

    def test_non_audio_verification_rejects_weak_security(self):
        for security in (bytes([2, 16]), bytes([3, 7]), bytes([3])):
            with self.subTest(security=security), \
                    patch.object(watcher, "desktop_sessions", return_value=[("16", True)]), \
                    patch.object(watcher, "phone_state", return_value=False), \
                    patch.object(watcher.socket, "socket") as socket_factory, \
                    patch.object(watcher, "signal_strength") as signal, \
                    patch.object(watcher, "command") as execute:
                socket_factory.return_value.__enter__.return_value.getsockopt.return_value = security
                self.assertFalse(watcher.request_unlock(ADDRESS, -45, auto_connect=True))
                signal.assert_not_called()
                execute.assert_not_called()

    def test_desktop_selection_excludes_other_users_and_remote_sessions(self):
        sessions = [{"uid": 1000, "class": "user", "session": "16"},
                    {"uid": 1001, "class": "user", "session": "17"},
                    {"uid": 1000, "class": "manager", "session": "18"}]
        with patch.object(watcher.os, "getuid", return_value=1000), \
                patch.object(watcher, "command", side_effect=[json.dumps(sessions), "Remote=yes\nType=wayland\nActive=yes\nLockedHint=yes"]):
            self.assertEqual(watcher.desktop_sessions(), [])
