import unittest
from unittest.mock import Mock

from bluez import Bluetooth


class PairingAgentTests(unittest.TestCase):
    def setUp(self):
        self.bluetooth = Bluetooth.__new__(Bluetooth)
        self.bluetooth.pairing_path = "/selected/device"
        self.bluetooth.confirm = Mock()
        self.bluetooth.notify = Mock()
        self.invocation = Mock()

    def test_other_device_is_rejected(self):
        self.bluetooth._handle_agent("RequestConfirmation", ("/other/device", 123456), self.invocation)
        self.invocation.return_dbus_error.assert_called_once()
        self.bluetooth.confirm.assert_not_called()

    def test_confirmation_requires_user_response(self):
        self.bluetooth._handle_agent("RequestConfirmation", ("/selected/device", 123), self.invocation)
        self.invocation.return_value.assert_not_called()
        method, code, respond = self.bluetooth.confirm.call_args.args
        self.assertEqual((method, code), ("RequestConfirmation", "000123"))
        respond(True)
        self.invocation.return_value.assert_called_once_with(None)

    def test_user_can_reject_pairing(self):
        self.bluetooth._handle_agent("RequestConfirmation", ("/selected/device", 123456), self.invocation)
        self.bluetooth.confirm.call_args.args[2](False)
        self.invocation.return_dbus_error.assert_called_once()
        self.invocation.return_value.assert_not_called()

    def test_invalid_passkey_is_rejected(self):
        self.bluetooth._handle_agent("RequestPasskey", ("/selected/device",), self.invocation)
        self.bluetooth.confirm.call_args.args[2](True, "not a number")
        self.invocation.return_dbus_error.assert_called_once()
