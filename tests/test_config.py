import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import DEFAULTS, config_path, load_config, save_config, validate_config


class ConfigurationTests(unittest.TestCase):
    def test_missing_configuration_disables_unlock(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load_config(Path(directory) / "missing.json"), DEFAULTS)

    def test_enabled_requires_device(self):
        with self.assertRaises(ValueError):
            validate_config({"enabled": True})

    def test_older_config_defaults_to_no_reconnection(self):
        settings = validate_config({"enabled": True, "device_address": "AA:BB:CC:DD:EE:FF"})
        self.assertFalse(settings["auto_connect"])

    def test_invalid_values_are_rejected(self):
        for values in ({"device_address": "bad"}, {"near_threshold": True},
                       {"near_threshold": 127}, {"enabled": "false"}, {"auto_connect": "false"}, {"version": 2}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_config(values)

    def test_atomic_save_and_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "settings/config.json"
            values = {"enabled": True, "device_address": "aa:bb:cc:dd:ee:ff", "device_name": "测试手机", "near_threshold": -50}
            saved = save_config(values, target)
            self.assertEqual(load_config(target), saved)
            self.assertEqual(saved["device_address"], "AA:BB:CC:DD:EE:FF")
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(len(list(target.parent.iterdir())), 1)

    def test_corrupt_config_does_not_fall_back_to_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "config.json"
            target.write_text("{broken", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(target)

    def test_xdg_config_location(self):
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": "/tmp/iphone-unlock-test"}):
            self.assertEqual(config_path(), Path("/tmp/iphone-unlock-test/iphone-unlock/config.json"))
