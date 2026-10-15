import argparse
import json
import logging
import os
import re
import socket
import struct
import subprocess
from contextlib import contextmanager

from config import load_config


BT_SECURITY = 4
BT_SECURITY_HIGH = 3
SDP_PSM = 1

def command(arguments, timeout=5):
    try:
        result = subprocess.run(
            arguments, capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def phone_state(address):
    output = command(["/usr/bin/bluetoothctl", "info", address])
    if output is None:
        return None
    properties = dict(re.findall(
        r"^\s*(Paired|Bonded|Connected): (yes|no)\s*$", output, re.MULTILINE,
    ))
    if properties.get("Paired") != "yes" or properties.get("Bonded") != "yes":
        return None
    if properties.get("Connected") not in {"yes", "no"}:
        return None
    return properties["Connected"] == "yes"


def signal_strength(address):
    output = command(["/usr/bin/hcitool", "rssi", address], timeout=3)
    match = re.search(r"RSSI return value: (-?\d+)", output or "")
    if match is None:
        return None
    strength = int(match.group(1))
    return strength if -127 <= strength < 127 else None


@contextmanager
def phone_link(address, auto_connect=False):
    connected = phone_state(address)
    if connected is None:
        raise ConnectionError("手机没有有效配对")
    if not auto_connect:
        if not connected:
            raise ConnectionError("手机未连接，已禁止主动重连")
        yield
        return
    with socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP) as channel:
        channel.settimeout(6)
        channel.setsockopt(socket.SOL_BLUETOOTH, BT_SECURITY, struct.pack("BB", BT_SECURITY_HIGH, 0))
        channel.connect((address, SDP_PSM))
        security = channel.getsockopt(socket.SOL_BLUETOOTH, BT_SECURITY, 2)
        if len(security) < 2 or security[0] < BT_SECURITY_HIGH or security[1] < 16:
            raise ConnectionError("手机的认证或加密级别不足")
        yield


def desktop_sessions():
    output = command(["/usr/bin/loginctl", "list-sessions", "--json=short"])
    if output is None:
        return []
    try:
        sessions = json.loads(output)
    except (ValueError, TypeError):
        return []
    desktops = []
    for session in sessions:
        if session.get("uid") != os.getuid() or session.get("class") != "user":
            continue
        session_id = str(session["session"])
        details = command([
            "/usr/bin/loginctl", "show-session", session_id,
            "-p", "Remote", "-p", "Type", "-p", "Active", "-p", "LockedHint",
        ])
        properties = dict(line.split("=", 1) for line in (details or "").splitlines() if "=" in line)
        if (properties.get("Remote") == "no"
                and properties.get("Active") == "yes"
                and properties.get("Type") in {"wayland", "x11"}):
            desktops.append((session_id, properties.get("LockedHint") == "yes"))
    return desktops


def request_unlock(address, threshold, auto_connect=False):
    targets = [session_id for session_id, locked in desktop_sessions() if locked]
    if not targets:
        logging.info("No active locked desktop; ignoring request")
        return False
    try:
        with phone_link(address, auto_connect):
            strength = signal_strength(address)
            logging.info("Verification rssi=%s threshold=%s", strength, threshold)
            if strength is None or strength < threshold:
                logging.info("Unlock denied: signal is missing or too weak")
                return False
            if phone_state(address) is not True:
                return False
            current_targets = {session_id for session_id, locked in desktop_sessions() if locked}
            accepted = False
            for session_id in targets:
                if session_id in current_targets:
                    result = command(["/usr/bin/loginctl", "unlock-session", session_id])
                    logging.info("Unlock request session=%s accepted=%s", session_id, result is not None)
                    accepted = accepted or result is not None
            return accepted
    except OSError as error:
        logging.info("Unlock denied: non-audio verification failed: %s", error)
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", help="Override device for --check only")
    parser.add_argument("--near", type=int, help="Override threshold for --check only")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--request", action="store_true")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    try:
        settings = load_config()
    except (OSError, ValueError) as error:
        logging.error("Cannot read configuration: %s", error)
        raise SystemExit(1)
    address = settings["device_address"]
    threshold = settings["near_threshold"]
    if arguments.check:
        address = arguments.address or address
        threshold = arguments.near if arguments.near is not None else threshold
        if address and not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", address):
            parser.error("Invalid Bluetooth address")
        if not -127 <= threshold < 127:
            parser.error("Invalid threshold")
        connected = phone_state(address) if address else None
        print(json.dumps({
            "enabled": settings["enabled"],
            "auto_connect": settings["auto_connect"],
            "device_address": address,
            "connected": connected,
            "rssi": signal_strength(address) if connected else None,
            "sessions": desktop_sessions(),
            "near_threshold": threshold,
        }, ensure_ascii=False))
        return
    if not settings["enabled"] or not address:
        logging.info("Unlock denied: disabled or no device configured")
        raise SystemExit(1)
    raise SystemExit(0 if request_unlock(address, threshold, auto_connect=settings["auto_connect"]) else 1)


if __name__ == "__main__":
    main()
