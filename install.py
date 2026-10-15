import argparse
import os
import shutil
import subprocess
from pathlib import Path

from config import DEFAULTS, config_path, load_config, save_config


UUID = "iphone-unlock@local"
APP_ID = "io.github.iphone_unlock.Settings"
FILES = ("config.py", "watcher.py", "bluez.py", "gui.py", "LICENSE", "VERSION")


def update_enabled(enable):
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio
    settings = Gio.Settings.new("org.gnome.shell")
    enabled = list(settings.get_strv("enabled-extensions"))
    disabled = list(settings.get_strv("disabled-extensions"))
    if enable:
        if UUID not in enabled:
            enabled.append(UUID)
        disabled = [value for value in disabled if value != UUID]
    else:
        enabled = [value for value in enabled if value != UUID]
    if not settings.set_strv("enabled-extensions", enabled):
        raise RuntimeError("Cannot update enabled extensions")
    if not settings.set_strv("disabled-extensions", disabled):
        raise RuntimeError("Cannot update disabled extensions")
    Gio.Settings.sync()


def install(source, migrate_device=None, migrate_name=""):
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk
    if shutil.which("hcitool") is None or shutil.which("bluetoothctl") is None:
        raise RuntimeError("请先安装 bluez")
    if subprocess.check_output(["gnome-shell", "--version"], text=True).split()[-1].split(".")[0] != "50":
        raise RuntimeError("当前只支持 GNOME Shell 50")
    library = Path.home() / ".local/lib/iphone-proximity-unlock"
    data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    extension = data / "gnome-shell/extensions" / UUID
    desktop = data / "applications" / f"{APP_ID}.desktop"
    for target in (library, extension, desktop.parent):
        target.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        shutil.copy2(source / name, library / name)
    for name in ("metadata.json", "extension.js"):
        shutil.copy2(source / "extension" / name, extension / name)
    settings = load_config()
    if migrate_device and not config_path().exists():
        settings = {**DEFAULTS, "enabled": True, "device_address": migrate_device, "device_name": migrate_name}
    save_config(settings)
    executable = str(library / "gui.py").replace("\\", "\\\\").replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%')
    desktop.write_text(
        "[Desktop Entry]\nType=Application\nName=iPhone Unlock\n"
        "Comment=Pair an iPhone and configure lock screen unlocking\n"
        "Comment[zh_CN]=配对 iPhone 并设置锁屏按钮解锁\n"
        f'Exec=/usr/bin/python3 "{executable}"\n'
        "Icon=bluetooth\nTerminal=false\nCategories=Settings;Security;\n"
        f"StartupWMClass={APP_ID}\n", encoding="utf-8",
    )
    update_enabled(True)
    print(f"已安装：{desktop}")
    print("在应用列表打开 iPhone Unlock。首次安装或更新扩展代码后，请自行注销并重新登录。")


def uninstall(purge=False):
    subprocess.run(["gnome-extensions", "disable", UUID], check=False)
    update_enabled(False)
    data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    for target in (Path.home() / ".local/lib/iphone-proximity-unlock", data / "gnome-shell/extensions" / UUID):
        if target.exists():
            shutil.rmtree(target)
    (data / "applications" / f"{APP_ID}.desktop").unlink(missing_ok=True)
    if purge:
        config_path().unlink(missing_ok=True)
    print("已卸载。" + ("配置已删除。" if purge else "保留了用户配置。"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["install", "uninstall"])
    parser.add_argument("--purge", action="store_true")
    parser.add_argument("--migrate-device")
    parser.add_argument("--migrate-name", default="")
    arguments = parser.parse_args()
    try:
        if arguments.action == "install":
            install(Path(__file__).resolve().parent, arguments.migrate_device, arguments.migrate_name)
        else:
            uninstall(arguments.purge)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"操作失败：{error}\n")


if __name__ == "__main__":
    main()
