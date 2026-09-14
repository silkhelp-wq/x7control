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
X7_EXACT_NAME = "sound blaster x7"
CREATIVE_OUI = "00:02:3C"   # Creative Technology's Bluetooth address prefix


def choose_x7(lines):
    """Pick the X7 out of bluetoothctl 'devices' lines: prefer a Creative address, then the
    exact product name. A random nearby device that merely has 'x7' in its name is never chosen
    over those, because pairing drops and replaces the bond of whatever is picked."""
    creative, exact = None, None
    for line in lines:
        m = DEVICE_RE.match(line.strip())
        if not m or not valid_mac(m.group(1)) or not X7_NAME_RE.search(m.group(2)):
            continue
        mac, name = m.group(1), m.group(2).strip()
        if mac.upper().startswith(CREATIVE_OUI) and creative is None:
            creative = (mac, name)
        elif name.lower() == X7_EXACT_NAME and exact is None:
            exact = (mac, name)
    return creative or exact or (None, None)


def _bt(*args, timeout=30):
    try:
        return subprocess.run(["bluetoothctl", *args], capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def find_x7():
    """(mac, name) of a known or scanned X7, else (None, None)."""
    return choose_x7(_bt("devices").splitlines() + _bt("devices", "Paired").splitlines())


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
        reason = "already paired" if "AlreadyExists" in out else ("authentication failed" if "Authentication" in out else "no answer")
        raise RuntimeError("Pairing failed (%s). Is the X7 blinking blue?" % reason)
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
