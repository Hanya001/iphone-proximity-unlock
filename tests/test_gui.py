import unittest
from unittest.mock import Mock, patch

from gui import SettingsWindow


class PassiveModeTests(unittest.TestCase):
    def test_disconnected_test_does_not_connect(self):
        window = Mock()
        window._current.return_value = {"address": "AA:BB:CC:DD:EE:FF", "connected": False}
        window.auto_connect_row.get_active.return_value = False
        def run(work, finished):
            try:
                result = work()
            except OSError as error:
                finished(None, error)
            else:
                finished(result, None)
        window._task.side_effect = run
        with patch("gui.phone_link") as link, patch("gui.signal_strength") as signal:
            link.return_value.__enter__.side_effect = ConnectionError("手机未连接，已禁止主动重连")
            SettingsWindow._test(window)
        window.bluetooth.connect.assert_not_called()
        signal.assert_not_called()
        link.assert_called_once_with("AA:BB:CC:DD:EE:FF", False)
        self.assertIn("禁止主动重连", window._notify.call_args.args[0])

    def test_connected_test_only_reads_signal(self):
        window = Mock()
        window._current.return_value = {"address": "AA:BB:CC:DD:EE:FF", "connected": True}
        window.auto_connect_row.get_active.return_value = False
        window._task.side_effect = lambda work, finished: finished(work(), None)
        with patch("gui.phone_link") as link, patch("gui.signal_strength", return_value=-30) as signal:
            SettingsWindow._test(window)
        link.assert_called_once_with("AA:BB:CC:DD:EE:FF", False)
        window.bluetooth.connect.assert_not_called()
        signal.assert_called_once_with("AA:BB:CC:DD:EE:FF")
        self.assertEqual(window.last_signal, -30)
