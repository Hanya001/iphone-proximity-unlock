import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import install
from config import load_config, save_config


class InstallationTests(unittest.TestCase):
    def test_install_preserves_settings_and_uninstall_retains_them(self):
        source = Path(install.__file__).parent
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(home / "config"), "XDG_DATA_HOME": str(home / "data")}), \
                    patch.object(Path, "home", return_value=home), \
                    patch.object(install, "update_enabled"), \
                    patch.object(install.shutil, "which", return_value="/usr/bin/tool"), \
                    patch.object(install.subprocess, "check_output", return_value="GNOME Shell 50.1"), \
                    patch.object(install.subprocess, "run"):
                save_config({"enabled": True, "device_address": "AA:BB:CC:DD:EE:FF", "near_threshold": -60})
                install.install(source)
                self.assertEqual(load_config()["near_threshold"], -60)
                self.assertTrue((home / ".local/lib/iphone-proximity-unlock/gui.py").exists())
                self.assertTrue((home / "data/applications/io.github.iphone_unlock.Settings.desktop").exists())
                install.uninstall()
                self.assertFalse((home / ".local/lib/iphone-proximity-unlock").exists())
                self.assertTrue(load_config()["enabled"])
                install.uninstall(purge=True)
                self.assertFalse(load_config()["enabled"])
