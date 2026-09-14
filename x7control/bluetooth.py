"""Pairing the X7 with bluetoothctl.

The X7 forgets its bond on a factory reset, and the PC-side bond then refuses the RFCOMM
control channel with EACCES. Pairing here means: find the X7 (scanning if needed), drop
any stale bond, pair, and leave the device *untrusted* so BlueZ never auto-connects its
A2DP audio profile behind the user's back.
"""
import re
import subprocess
import time

from .soundcore import valid_mac

DEVICE_RE = re.compile(r"^Device ([0-9A-F:]{17}) (.*)$")
X7_NAME_RE = re.compile(r"x7|blaster", re.I)


def _bt(*args, timeout=30):
    try:
        return subprocess.run(["bluetoothctl", *args], capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def find_x7():
    """(mac, name) of a known or scanned X7, else (None, None)."""
    for line in _bt("devices").splitlines() + _bt("devices", "Paired").splitlines():
        m = DEVICE_RE.match(line.strip())
        if m and X7_NAME_RE.search(m.group(2)) and valid_mac(m.group(1)):
            return m.group(1), m.group(2)
    return None, None


def _scan(seconds, until):
    proc = subprocess.Popen(["bluetoothctl", "--timeout", str(int(seconds)), "scan", "on"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(int(seconds)):
            time.sleep(1)
            if until():
                break
    finally:
        proc.terminate()


def pair(status=lambda msg: None, scan_seconds=40):
    """Pair with the X7. Returns its MAC. Raises RuntimeError with a user-facing message."""
    mac, name = find_x7()
    if not mac:
        status("Scanning for the X7 (hold its Power/Bluetooth button 2 s until it blinks blue)…")
        _scan(scan_seconds, lambda: find_x7()[0])
        mac, name = find_x7()
    if not mac:
        raise RuntimeError("X7 not found. Make sure it is powered on and blinking blue (pairing mode).")
    status("Found %s at %s" % (name, mac))
    _bt("remove", mac)          # drop any stale bond (the X7 forgets us on a factory reset)
    time.sleep(1)
    _scan(15, lambda: find_x7()[0])
    out = _bt("pair", mac, timeout=60)
    if "Pairing successful" not in out and "already paired" not in out.lower():
        detail = [line for line in out.splitlines() if "Failed" in line or "Pairing" in line]
        raise RuntimeError(detail[-1].strip() if detail else "Pairing failed. Is the X7 blinking blue?")
    _bt("untrust", mac)         # do not auto-connect audio; the app only needs the bond
    status("Paired with %s" % mac)
    return mac


def info(mac):
    """Paired / trusted / connected flags for a device, from bluetoothctl info."""
    if not valid_mac(mac):
        return {}
    out = _bt("info", mac)
    flags = {}
    for key in ("Paired", "Trusted", "Connected", "Name"):
        m = re.search(r"^\s*%s:\s*(.+)$" % key, out, re.M)
        if m:
            flags[key.lower()] = m.group(1).strip()
    return flags
