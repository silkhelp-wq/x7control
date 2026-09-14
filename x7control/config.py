"""User configuration: ~/.config/x7control/config.json (XDG_CONFIG_HOME honoured).

Only what the app needs is kept: the X7's Bluetooth address, EQ presets and a few flags.
Everything read back from disk is validated, so a hand-edited or corrupted file
degrades to defaults instead of crashing the app or reaching a subprocess.
"""
import json
import os
import re

from .soundcore import valid_mac

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "x7control")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
LEGACY_X7CTL_CONFIG = os.path.join(os.path.dirname(CONFIG_DIR), "x7ctl", "config.json")

NODE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,200}$")
PRESET_NAME_RE = re.compile(r"[^\w .()+-]")
FILTER_TYPES = ("peaking", "lowshelf", "highshelf", "lowpass", "highpass", "notch")

DEFAULTS = {
    "mac": None,
    "pc_eq_presets": [],
    "box_eq_presets": [],
    "pc_eq_enabled": True,
    "voice_source": None,       # raw microphone node the voice filter wraps
}


def clean_name(name, limit=40):
    """Preset names end up in config files and UI labels: keep them plain."""
    return PRESET_NAME_RE.sub("", str(name)).strip()[:limit] or "preset"


def valid_node_name(name):
    return isinstance(name, str) and bool(NODE_NAME_RE.match(name))


def _num(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    if v != v:  # NaN
        return default
    return max(lo, min(hi, v))


def clean_pc_preset(p):
    """Validate a parametric-EQ preset: 10 bands with sane type/freq/Q/gain, preamp in range."""
    if not isinstance(p, dict) or not isinstance(p.get("bands"), list):
        return None
    bands = []
    for b in p["bands"][:10]:
        if not isinstance(b, dict):
            continue
        t = b.get("type") if b.get("type") in FILTER_TYPES else "peaking"
        bands.append({"type": t, "freq": _num(b.get("freq"), 20, 20000, 1000.0),
                      "q": _num(b.get("q"), 0.1, 12, 1.0), "gain": _num(b.get("gain"), -30, 30, 0.0)})
    while len(bands) < 10:
        bands.append({"type": "peaking", "freq": 1000.0, "q": 1.0, "gain": 0.0})
    return {"name": clean_name(p.get("name", "preset")), "preamp": _num(p.get("preamp"), -30, 10, 0.0), "bands": bands}


def clean_box_preset(p):
    """Validate a 10-band graphic EQ preset for the X7's own equalizer."""
    if not isinstance(p, dict) or not isinstance(p.get("bands"), list):
        return None
    bands = [_num(g, -24, 24, 0.0) for g in p["bands"][:10]]
    while len(bands) < 10:
        bands.append(0.0)
    return {"name": clean_name(p.get("name", "preset")), "preamp": _num(p.get("preamp"), -12, 12, 0.0), "bands": bands}


def _clean_list(items, fn):
    out, seen = [], set()
    for p in items if isinstance(items, list) else []:
        c = fn(p)
        if c and c["name"] not in seen:
            seen.add(c["name"])
            out.append(c)
    return out


def load():
    cfg = dict(DEFAULTS)
    raw = {}
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    mac = raw.get("mac")
    if not valid_mac(mac):
        mac = None
        try:
            with open(LEGACY_X7CTL_CONFIG, encoding="utf-8") as f:
                legacy = json.load(f).get("mac")
            if valid_mac(legacy):
                mac = legacy
        except (OSError, ValueError, AttributeError):
            pass
    cfg["mac"] = mac.upper() if mac else None
    cfg["pc_eq_presets"] = _clean_list(raw.get("pc_eq_presets"), clean_pc_preset)
    cfg["box_eq_presets"] = _clean_list(raw.get("box_eq_presets"), clean_box_preset)
    cfg["pc_eq_enabled"] = bool(raw.get("pc_eq_enabled", True))
    vs = raw.get("voice_source")
    cfg["voice_source"] = vs if valid_node_name(vs) else None
    return cfg


def save(cfg):
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_FILE)
