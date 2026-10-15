import json
import os
import re
import tempfile
from pathlib import Path


DEFAULTS = {
    "version": 1,
    "enabled": False,
    "auto_connect": False,
    "device_address": "",
    "device_name": "",
    "near_threshold": -45,
}


def config_path():
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "iphone-unlock" / "config.json"


def validate_config(values):
    if not isinstance(values, dict):
        raise ValueError("配置必须是 JSON 对象")
    settings = {**DEFAULTS, **values}
    if type(settings["version"]) is not int or settings["version"] != 1:
        raise ValueError("不支持的配置版本")
    if type(settings["enabled"]) is not bool:
        raise ValueError("启用状态必须是布尔值")
    if type(settings["auto_connect"]) is not bool:
        raise ValueError("主动重连状态必须是布尔值")
    address = settings["device_address"]
    if not isinstance(address, str) or (address and not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", address)):
        raise ValueError("蓝牙地址格式不正确")
    settings["device_address"] = address.upper()
    if settings["enabled"] and not address:
        raise ValueError("请先选择并配对设备")
    if not isinstance(settings["device_name"], str) or len(settings["device_name"]) > 128:
        raise ValueError("设备名称过长或格式不正确")
    threshold = settings["near_threshold"]
    if type(threshold) is not int or not -127 <= threshold < 127:
        raise ValueError("RSSI 阈值必须在 -127 到 126 之间")
    return {key: settings[key] for key in DEFAULTS}


def load_config(path=None):
    target = Path(path) if path is not None else config_path()
    try:
        return validate_config(json.loads(target.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return DEFAULTS.copy()


def save_config(values, path=None):
    settings = validate_config(values)
    target = Path(path) if path is not None else config_path()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, delete=False) as output:
            temporary = Path(output.name)
            json.dump(settings, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return settings
