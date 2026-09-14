"""PipeWire side of X7 Control.

Three optional filter chains live in ~/.config/pipewire/pipewire.conf.d/ and are written by
this module (never by hand-edited templates with user text inside them):

  x7control-headphone-eq.conf   parametric EQ, WirePlumber "smart" filter in front of the X7 sink
  x7control-game-surround.conf  virtual 7.1 sink rendered binaurally (SOFA HRTF) into the EQ
  x7control-voice-filter.conf   RNNoise voice-only microphone wrapping a chosen input source

Live control goes through pw-cli / pw-dump / wpctl. No pactl, so pulseaudio-utils is not
needed. Node names that come from the user's config are validated before they are
interpolated into anything.
"""
import glob
import json
import math
import os
import re
import subprocess

from .config import clean_name, valid_node_name
from .soundcore import safe_float

CONF_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "pipewire", "pipewire.conf.d")
EQ_CONF = os.path.join(CONF_DIR, "x7control-headphone-eq.conf")
SURROUND_CONF = os.path.join(CONF_DIR, "x7control-game-surround.conf")
VOICE_CONF = os.path.join(CONF_DIR, "x7control-voice-filter.conf")

EQ_NODE = "effect_input.x7control-eq"
SURROUND_NODE = "effect_input.x7control-surround"
VOICE_NODE = "x7control-voice"                 # the virtual microphone apps see
VOICE_FILTER_NODE = "capture.x7control-voice"  # the filter's capture side (holds the RNNoise params)

X7_SINK_RE = re.compile(r"alsa_output\.usb-Creative_Technology_Ltd_Sound_Blaster_X7_[A-Za-z0-9_.-]*analog-stereo")
X7_CARD_NAME = "Sound Blaster X7"

FILTER_TYPES = {"peaking": "bq_peaking", "lowshelf": "bq_lowshelf", "highshelf": "bq_highshelf",
                "lowpass": "bq_lowpass", "highpass": "bq_highpass", "notch": "bq_notch"}  # keys mirror config.FILTER_TYPES
FILTER_LABELS = {v: k for k, v in FILTER_TYPES.items()}

VAD_THRESHOLD = "rnnoise:VAD Threshold (%)"
VAD_GRACE = "rnnoise:VAD Grace Period (ms)"

# AutoEq (oratory1990, Harman over-ear target) Sennheiser HD 598 CS - a built-in example preset
HD598CS_PRESET = {
    "name": "AutoEq HD 598 CS",
    "preamp": -4.8,
    "bands": [
        {"type": "lowshelf", "freq": 105.0, "q": 0.70, "gain": -2.1},
        {"type": "peaking", "freq": 10000.0, "q": 0.40, "gain": 6.1},
        {"type": "peaking", "freq": 1121.0, "q": 0.57, "gain": -2.7},
        {"type": "peaking", "freq": 178.0, "q": 1.12, "gain": -3.9},
        {"type": "peaking", "freq": 297.0, "q": 1.74, "gain": 4.2},
        {"type": "highshelf", "freq": 10000.0, "q": 0.70, "gain": -3.6},
        {"type": "peaking", "freq": 3480.0, "q": 5.91, "gain": -3.3},
        {"type": "peaking", "freq": 4410.0, "q": 4.27, "gain": 1.7},
        {"type": "peaking", "freq": 2845.0, "q": 5.08, "gain": 1.7},
        {"type": "peaking", "freq": 55.0, "q": 1.59, "gain": -0.4},
    ],
}
FLAT_PRESET = {"name": "Flat (bypass)", "preamp": 0.0,
               "bands": [{"type": "peaking", "freq": float(f), "q": 1.0, "gain": 0.0}
                         for f in (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)]}
BUILTIN_PRESETS = [FLAT_PRESET, HD598CS_PRESET]


def _run(cmd, timeout=15):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(cmd, 1, "", str(e))


def _write_atomic(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _quote(s):
    """Quote a string for a PipeWire .conf file (SPA JSON).

    Inside a quoted SPA-JSON string only backslash and double quote are special, and the
    parser rejects control characters outright (which would make PipeWire drop the whole
    file), so controls become spaces and the two specials are escaped.
    """
    s = "".join(c if ord(c) >= 32 and c != "\x7f" else " " for c in str(s))[:200]
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _f(v, lo, hi, default):
    """Float from graph/config data, clamped; garbage -> default."""
    return safe_float(v, lo, hi, default)


# ---- graph queries ------------------------------------------------------------
def pw_dump():
    try:
        dump = json.loads(_run(["pw-dump"]).stdout or "[]")
    except (json.JSONDecodeError, RecursionError):
        return []
    return [o for o in dump if isinstance(o, dict)] if isinstance(dump, list) else []


def _props(o):
    info = o.get("info") if isinstance(o.get("info"), dict) else {}
    props = info.get("props")
    return props if isinstance(props, dict) else {}


def _str(v):
    return v if isinstance(v, str) else ""


def _is_node(o):
    return o.get("type") == "PipeWire:Interface:Node" and isinstance(o.get("id"), int)


def nodes(dump=None):
    """[{id, name, description, class}] for every node in the graph."""
    out = []
    for o in dump if dump is not None else pw_dump():
        if not _is_node(o):
            continue
        props = _props(o)
        name = _str(props.get("node.name"))
        out.append({"id": o["id"], "name": name,
                    "description": _str(props.get("node.description")) or _str(props.get("node.nick")) or name,
                    "class": _str(props.get("media.class")), "props": props})
    return out


def find_node(name_or_re, dump=None):
    if not name_or_re:
        return None
    for o in dump if dump is not None else pw_dump():
        if not _is_node(o):
            continue
        n = _str(_props(o).get("node.name"))
        if n == name_or_re or (hasattr(name_or_re, "fullmatch") and name_or_re.fullmatch(n)):
            return o
    return None


def node_props_params(node):
    """The flat control list of a filter-chain node as a dict ('eq1:Gain': -2.1, ...).

    pw-dump lists several Props objects per node (stream volume/channelmix first, the filter
    controls in a later one), so every 'params' list is merged. Values are returned raw;
    callers clamp them with _f().
    """
    out = {}
    info = node.get("info") if isinstance(node.get("info"), dict) else {}
    params = info.get("params") if isinstance(info.get("params"), dict) else {}
    for pr in params.get("Props") or []:
        lst = pr.get("params") if isinstance(pr, dict) else None
        if isinstance(lst, list):
            for i in range(0, len(lst) - 1, 2):
                if isinstance(lst[i], str):
                    out[lst[i]] = lst[i + 1]
    return out


def defaults(dump=None):
    """{'sink': name, 'source': name} from the session's default metadata."""
    out = {}
    for o in dump if dump is not None else pw_dump():
        if o.get("type") != "PipeWire:Interface:Metadata":
            continue
        props = o.get("props") if isinstance(o.get("props"), dict) else {}
        if props.get("metadata.name") != "default":
            continue
        for m in o.get("metadata") or []:
            if not isinstance(m, dict):
                continue
            key = m.get("key")
            val = m.get("value")
            if isinstance(val, dict):
                val = val.get("name")
            if not isinstance(val, str):
                continue
            if key == "default.audio.sink":
                out["sink"] = val
            elif key == "default.audio.source":
                out["source"] = val
    return out


def sinks(dump=None):
    return [n for n in nodes(dump) if n["class"] == "Audio/Sink"]


def sources(dump=None):
    return [n for n in nodes(dump) if n["class"] in ("Audio/Source", "Audio/Source/Virtual")]


def x7_sink(dump=None):
    for n in sinks(dump):
        if X7_SINK_RE.fullmatch(n["name"]):
            return n
    return None


def set_param(node_id, items):
    """pw-cli set-param <node> Props { params = [ "name" value ... ] } — values are numbers only."""
    parts = []
    for k, v in items:
        parts.append('"%s" %.6f' % (re.sub(r'[\\"]', "", k), float(v)))
    r = _run(["pw-cli", "set-param", str(int(node_id)), "Props", "{ params = [ %s ] }" % " ".join(parts)])
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "pw-cli failed")


# ---- wpctl: volume / mute / defaults --------------------------------------------
def get_volume(node_id):
    """(percent, muted) via wpctl."""
    txt = _run(["wpctl", "get-volume", str(int(node_id))]).stdout
    m = re.search(r"Volume:\s*([\d.]+)", txt)
    return (int(round(float(m.group(1)) * 100)) if m else 0), "[MUTED]" in txt


def set_volume(node_id, percent):
    pct = max(0, min(150, int(percent)))
    return _run(["wpctl", "set-volume", str(int(node_id)), "%d%%" % pct]).returncode == 0


def set_mute(node_id, muted):
    return _run(["wpctl", "set-mute", str(int(node_id)), "1" if muted else "0"]).returncode == 0


def set_default(node_id):
    return _run(["wpctl", "set-default", str(int(node_id))]).returncode == 0


def restart():
    return _run(["systemctl", "--user", "restart", "pipewire", "pipewire-pulse", "wireplumber"], timeout=30).returncode == 0


# ---- headphone EQ ---------------------------------------------------------------
def eq_node_params(dump=None):
    node = find_node(EQ_NODE, dump)
    if not node:
        return None, {}
    return node["id"], node_props_params(node)


def read_live_eq():
    """Current live EQ as a preset dict (types come from the config file, values from the graph)."""
    node_id, p = eq_node_params()
    conf = read_conf_preset()
    if node_id is None or not p:
        return conf
    bands = []
    for i in range(1, 11):
        ctype = conf["bands"][i - 1]["type"] if conf and len(conf["bands"]) >= i else "peaking"
        bands.append({"type": ctype, "freq": _f(p.get("eq%d:Freq" % i), 20, 20000, 1000.0),
                      "q": _f(p.get("eq%d:Q" % i), 0.1, 12, 1.0), "gain": _f(p.get("eq%d:Gain" % i), -30, 30, 0.0)})
    mult = _f(p.get("preamp:Mult"), 0.0, 10.0, 1.0)
    preamp = 20 * math.log10(mult) if mult > 0 else -30.0
    return {"name": "Live", "preamp": round(preamp, 2), "bands": bands}


def apply_live_eq(preset, bypass=False):
    """Push freq/Q/gain (and preamp) into the running filter chain. No restart needed."""
    node_id, _ = eq_node_params()
    if node_id is None:
        raise RuntimeError("Headphone EQ filter is not running")
    mult = 1.0 if bypass else 10 ** (preset["preamp"] / 20.0)
    items = [("preamp:Mult", mult)]
    for i, b in enumerate(preset["bands"][:10], 1):
        items += [("eq%d:Freq" % i, b["freq"]), ("eq%d:Q" % i, b["q"]), ("eq%d:Gain" % i, 0.0 if bypass else b["gain"])]
    set_param(node_id, items)


def eq_conf_present():
    return os.path.exists(EQ_CONF)


def read_conf_preset():
    """Parse the filter types/values out of the config file (fallback when the graph is down)."""
    try:
        with open(EQ_CONF, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return json.loads(json.dumps(FLAT_PRESET))
    bands = []
    for m in re.finditer(r'label\s*=\s*(bq_\w+)\s+name\s*=\s*eq(\d{1,2})\s+control\s*=\s*\{\s*"Freq"\s*=\s*([\d.]{1,12})\s+"Q"\s*=\s*([\d.]{1,12})\s+"Gain"\s*=\s*(-?[\d.]{1,12})', text):
        bands.append((int(m.group(2)), {"type": FILTER_LABELS.get(m.group(1), "peaking"),
                                        "freq": _f(m.group(3), 20, 20000, 1000.0), "q": _f(m.group(4), 0.1, 12, 1.0),
                                        "gain": _f(m.group(5), -30, 30, 0.0)}))
    bands = [b for _, b in sorted(bands)]
    m = re.search(r'"Mult"\s*=\s*([\d.]{1,12})', text)
    mult = _f(m.group(1) if m else 1.0, 0.0, 10.0, 1.0)
    preamp = 20 * math.log10(mult) if mult > 0 else 0.0
    if len(bands) != 10:
        return json.loads(json.dumps(FLAT_PRESET))
    return {"name": "Saved default", "preamp": round(preamp, 2), "bands": bands}


def eq_conf_text(preset):
    mult = 10 ** (float(preset["preamp"]) / 20.0)
    lines = ['                    { type = builtin label = linear      name = preamp control = { "Mult" = %.4f } }' % mult]
    for i, b in enumerate(preset["bands"][:10], 1):
        lines.append('                    { type = builtin label = %-12s name = eq%-2d control = { "Freq" = %-8.1f "Q" = %.2f "Gain" = %5.1f } }'
                     % (FILTER_TYPES.get(b["type"], "bq_peaking"), i, float(b["freq"]), float(b["q"]), float(b["gain"])))
    links = ['                    { output = "preamp:Out" input = "eq1:In" }']
    for i in range(1, 10):
        links.append('                    { output = "eq%d:Out"    input = "eq%d:In" }' % (i, i + 1))
    return """# Headphone correction in front of the Sound Blaster X7 - transparent "smart" filter.
# Written by X7 Control (preset: %s). Preamp %.1f dB.
# WirePlumber inserts this between every stream and the X7 sink automatically.
# Freq/Q/Gain changes are applied live by X7 Control; changing a filter TYPE
# needs: systemctl --user restart pipewire pipewire-pulse wireplumber

context.modules = [
    { name = libpipewire-module-filter-chain
        flags = [ nofail ]
        args = {
            node.description = "X7 Headphone Correction"
            media.name       = "X7 Headphone Correction"
            filter.graph = {
                nodes = [
%s
                ]
                links = [
%s
                ]
                inputs  = [ "preamp:In" ]
                outputs = [ "eq10:Out" ]
            }
            audio.channels = 2
            audio.position = [ FL FR ]
            capture.props = {
                node.name           = "%s"
                media.class         = Audio/Sink
                filter.smart        = true
                filter.smart.name   = "x7control-eq"
                filter.smart.target = { media.class = "Audio/Sink" alsa.card_name = "%s" }
            }
            playback.props = {
                node.name    = "effect_output.x7control-eq"
                node.passive = true
            }
        }
    }
]
""" % (clean_name(preset.get("name", "custom")), float(preset["preamp"]), "\n".join(lines), "\n".join(links), EQ_NODE, X7_CARD_NAME)


def write_eq_conf(preset):
    """Rewrite the EQ config so it survives a PipeWire restart / reboot."""
    _write_atomic(EQ_CONF, eq_conf_text(preset))


def remove_eq_conf():
    try:
        os.remove(EQ_CONF)
    except FileNotFoundError:
        pass


def parse_autoeq(text):
    """Parse an AutoEq ParametricEQ.txt ("Preamp: -4.8 dB", "Filter 1: ON PK Fc 105 Hz Gain -2.1 dB Q 0.70")."""
    m = re.search(r"Preamp:\s*(-?[\d.]{1,12})\s*dB", text)
    preamp = _f(m.group(1) if m else 0.0, -30, 10, 0.0)
    bands = []
    kinds = {"PK": "peaking", "LSC": "lowshelf", "HSC": "highshelf", "LS": "lowshelf", "HS": "highshelf",
             "LP": "lowpass", "HP": "highpass", "NO": "notch"}
    for m in re.finditer(r"Filter\s*\d+:\s*(ON|OFF)\s+(\w+)\s+Fc\s+([\d.]{1,12})\s*Hz\s+Gain\s+(-?[\d.]{1,12})\s*dB(?:\s+Q\s+([\d.]{1,12}))?", text):
        if m.group(1) != "ON":
            continue
        bands.append({"type": kinds.get(m.group(2), "peaking"), "freq": _f(m.group(3), 20, 20000, 1000.0),
                      "gain": _f(m.group(4), -30, 30, 0.0), "q": _f(m.group(5) or 0.7, 0.1, 12, 0.7)})
    while len(bands) < 10:
        bands.append({"type": "peaking", "freq": 1000.0, "q": 1.0, "gain": 0.0})
    return {"name": "Imported", "preamp": preamp, "bands": bands[:10]}


# ---- game surround (HRTF 7.1) -----------------------------------------------------
SOFA_CANDIDATES = ["/usr/share/libmysofa/default.sofa", "/usr/share/libmysofa/MIT_KEMAR_normal_pinna.sofa",
                   "/usr/lib/libmysofa/default.sofa", "/usr/share/pipewire/default.sofa"]


def find_sofa():
    for p in SOFA_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def surround_conf_present():
    return os.path.exists(SURROUND_CONF)


def surround_conf_text(sofa):
    spk = [("spFL", 30.0), ("spFR", 330.0), ("spFC", 0.0), ("spRL", 150.0), ("spRR", 210.0), ("spSL", 90.0), ("spSR", 270.0)]
    nodes_txt = []
    for name, az in spk:
        nodes_txt.append('                    { type = sofa label = spatializer name = %s\n'
                         '                      config = { filename = %s blocksize = 256 tailsize = 1024 }\n'
                         '                      control = { "Azimuth" = %.1f "Elevation" = 0.0 "Radius" = 1.0 } }' % (name, _quote(sofa), az))
    nodes_txt.append('                    { type = builtin label = copy name = lfe }')
    gains = '"Gain 1" = 0.5 "Gain 2" = 0.5 "Gain 3" = 0.5 "Gain 4" = 0.35 "Gain 5" = 0.5 "Gain 6" = 0.5 "Gain 7" = 0.5 "Gain 8" = 0.5'
    nodes_txt.append('                    { type = builtin label = mixer name = mixL control = { %s } }' % gains)
    nodes_txt.append('                    { type = builtin label = mixer name = mixR control = { %s } }' % gains)
    order = ["spFL", "spFR", "spFC", "lfe", "spRL", "spRR", "spSL", "spSR"]
    links = []
    for side, mixer in (("L", "mixL"), ("R", "mixR")):
        for i, n in enumerate(order, 1):
            port = "lfe:Out" if n == "lfe" else "%s:Out %s" % (n, side)
            links.append('                    { output = "%s" input = "%s:In %d" }' % (port, mixer, i))
    return """# "Sound Blaster X7 Game Surround (HRTF 7.1)" - optional virtual 7.1 sink.
# Written by X7 Control. Games see a real 7.1 output; each speaker channel is binaurally
# rendered with the SOFA HRTF below and mixed to stereo into the headphone EQ.
# Not the default sink on purpose: it colours music and voice chat. Pick it per game.

context.modules = [
    { name = libpipewire-module-filter-chain
        flags = [ nofail ]
        args = {
            node.description = "Sound Blaster X7 Game Surround (HRTF 7.1)"
            media.name       = "Sound Blaster X7 Game Surround (HRTF 7.1)"
            filter.graph = {
                nodes = [
%s
                ]
                links = [
%s
                ]
                inputs  = [ "spFL:In" "spFR:In" "spFC:In" "lfe:In" "spRL:In" "spRR:In" "spSL:In" "spSR:In" ]
                outputs = [ "mixL:Out" "mixR:Out" ]
            }
            capture.props = {
                node.name      = "%s"
                media.class    = Audio/Sink
                audio.channels = 8
                audio.position = [ FL FR FC LFE RL RR SL SR ]
            }
            playback.props = {
                node.name      = "effect_output.x7control-surround"
                node.passive   = true
                audio.channels = 2
                audio.position = [ FL FR ]
                target.object  = "%s"
            }
        }
    }
]
""" % ("\n".join(nodes_txt), "\n".join(links), SURROUND_NODE, EQ_NODE)


def write_surround_conf():
    sofa = find_sofa()
    if not sofa:
        raise RuntimeError("No SOFA HRTF file found (install libmysofa)")
    _write_atomic(SURROUND_CONF, surround_conf_text(sofa))


def remove_surround_conf():
    try:
        os.remove(SURROUND_CONF)
    except FileNotFoundError:
        pass


# ---- voice filter (RNNoise) ---------------------------------------------------------
RNNOISE_CANDIDATES = ["/usr/lib/ladspa/librnnoise_ladspa.so", "/usr/lib64/ladspa/librnnoise_ladspa.so",
                      "/usr/lib/x86_64-linux-gnu/ladspa/librnnoise_ladspa.so", "/usr/lib/aarch64-linux-gnu/ladspa/librnnoise_ladspa.so",
                      "/usr/local/lib/ladspa/librnnoise_ladspa.so", os.path.expanduser("~/.local/lib/ladspa/librnnoise_ladspa.so")]


def find_rnnoise():
    for p in RNNOISE_CANDIDATES:
        if os.path.exists(p):
            return p
    for d in (os.environ.get("LADSPA_PATH") or "").split(":"):
        if d:
            for p in glob.glob(os.path.join(d, "librnnoise_ladspa*.so")):
                return p
    return None


def voice_conf_present():
    return os.path.exists(VOICE_CONF)


def voice_conf_source():
    """The raw source node the installed voice filter wraps, from its config file."""
    try:
        with open(VOICE_CONF, encoding="utf-8") as f:
            m = re.search(r'target\.object\s*=\s*"([^"\n]+)"', f.read())
    except OSError:
        return None
    return m.group(1) if m and valid_node_name(m.group(1)) else None


def voice_conf_text(source, description, plugin, threshold=85.0, grace=250.0):
    if not valid_node_name(source):
        raise ValueError("invalid source node name")
    return """# Voice-only virtual microphone written by X7 Control.
# Chain: 90 Hz high-pass -> RNNoise (noise-suppression-for-voice) with a voice-activity gate.
#   VAD Threshold (%%)   how sure the model must be that a frame is speech before it passes.
#   VAD Grace Period    keeps the gate open this long after the last detected speech.
# Both are tuned live from X7 Control; the values here are the startup defaults.

context.modules = [
    { name = libpipewire-module-filter-chain
        flags = [ nofail ]
        args = {
            node.description = %s
            media.name       = %s
            filter.graph = {
                nodes = [
                    { type = builtin label = bq_highpass name = hp
                      control = { "Freq" = 90.0 "Q" = 0.7 } }
                    { type = ladspa name = rnnoise
                      plugin = %s
                      label  = noise_suppressor_mono
                      control = { "VAD Threshold (%%)" = %.1f "VAD Grace Period (ms)" = %.1f "Retroactive VAD Grace (ms)" = 0.0 } }
                ]
                links = [
                    { output = "hp:Out" input = "rnnoise:Input" }
                ]
                inputs  = [ "hp:In" ]
                outputs = [ "rnnoise:Output" ]
            }
            audio.channels = 1
            audio.position = [ MONO ]
            audio.rate     = 48000
            capture.props = {
                node.name          = "%s"
                node.passive       = true
                target.object      = "%s"
                stream.dont-remix  = true
            }
            playback.props = {
                node.name        = "%s"
                node.description = %s
                media.class      = Audio/Source
                audio.channels   = 1
                audio.position   = [ MONO ]
            }
        }
    }
]
""" % (_quote(description), _quote(description), _quote(plugin), float(threshold), float(grace),
       VOICE_FILTER_NODE, source, VOICE_NODE, _quote(description))


def write_voice_conf(source, description, threshold=85.0, grace=250.0):
    plugin = find_rnnoise()
    if not plugin:
        raise RuntimeError("RNNoise LADSPA plugin not found (librnnoise_ladspa.so from noise-suppression-for-voice)")
    _write_atomic(VOICE_CONF, voice_conf_text(source, "%s (voice only)" % description, plugin, threshold, grace))


def remove_voice_conf():
    try:
        os.remove(VOICE_CONF)
    except FileNotFoundError:
        pass


def voice_filter_state(dump=None):
    """(node_id, vad_threshold, grace_ms) of the running RNNoise filter, or (None, 85, 250)."""
    node = find_node(VOICE_FILTER_NODE, dump)
    if not node:
        return None, 85.0, 250.0
    p = node_props_params(node)
    return node["id"], _f(p.get(VAD_THRESHOLD), 0, 100, 85.0), _f(p.get(VAD_GRACE), 0, 2000, 250.0)


def voice_filter_set(threshold, grace):
    node_id, _, _ = voice_filter_state()
    if node_id is None:
        raise RuntimeError("Voice filter is not running")
    set_param(node_id, [(VAD_THRESHOLD, max(0.0, min(100.0, threshold))), (VAD_GRACE, max(0.0, min(2000.0, grace)))])


def loopback_command(source, sink, name="x7control-monitor"):
    """pw-loopback argv that plays `source` into `sink` (live mic monitor)."""
    if not (valid_node_name(source) and valid_node_name(sink)):
        raise ValueError("invalid node name")
    # values are quoted: a ':' in a bare SPA-JSON value would otherwise end it early
    return ["pw-loopback", "--capture-props", 'target.object="%s" stream.dont-remix=true' % source,
            "--playback-props", 'target.object="%s" node.name="%s"' % (sink, name),
            "--channels", "1", "--name", "X7 Control mic monitor"]
