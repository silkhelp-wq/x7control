"""Equalizer maths and plain-language descriptions, shared by the graph widget and the UI.

Frequency responses use the RBJ Audio EQ Cookbook biquads, the same formulas PipeWire's
builtin bq_* filters implement, so the curve on screen is what the filter does.
"""
import math

SAMPLE_RATE = 48000.0
F_MIN, F_MAX = 20.0, 20000.0

# What a listener hears in each region. (low, high, name, what it affects)
ZONES = [
    (20, 60, "Sub-bass", "rumble you feel more than hear: explosions, cinema LFE"),
    (60, 250, "Bass", "kick drum thump, bass guitar weight, gunfire punch"),
    (250, 500, "Low mids", "warmth and fullness; too much sounds muddy or boomy"),
    (500, 2000, "Mids", "the body of voices and instruments; too much sounds boxy or nasal"),
    (2000, 4000, "Upper mids", "clarity and attack: footsteps, dialogue intelligibility"),
    (4000, 6000, "Presence", "detail and edge; too much gets harsh and tiring"),
    (6000, 20000, "Treble / air", "cymbal sparkle, 's' sounds, openness; a boost here can hiss"),
]

ZONE_SHORT = {"Sub-bass": "Sub", "Bass": "Bass", "Low mids": "Lo-mid", "Mids": "Mids", "Upper mids": "Hi-mid",
              "Presence": "Pres", "Treble / air": "Treble"}

# The X7's 10 fixed bands, one hint each (index matches soundcore.EQ_BAND_HZ)
GRAPHIC_BANDS = [
    (31, "Sub-bass", "Rumble and cinema bass you feel more than hear"),
    (62, "Bass", "Kick drum and explosion thump"),
    (125, "Upper bass", "Warmth and body; boomy when overdone"),
    (250, "Low mids", "Fullness; muddy when overdone"),
    (500, "Mids", "Body of voices and instruments; boxy when overdone"),
    (1000, "Mids", "Where hearing is most sensitive; the honky, nasal region"),
    (2000, "Upper mids", "Clarity and attack: footsteps, speech intelligibility"),
    (4000, "Presence", "Detail and bite; harsh and tiring when overdone"),
    (8000, "Treble", "Cymbal sparkle and 's' sounds; sibilant when overdone"),
    (16000, "Air", "Openness and shimmer; most adults hear little above this"),
]
GRAPHIC_Q = 1.41   # roughly one-octave bells, what a 10-band graphic EQ approximates

FILTER_HELP = {
    "peaking": "Bell: boosts or cuts a bump around the frequency. Q sets how wide the bump is.",
    "lowshelf": "Low shelf: moves everything below the frequency up or down together (a bass knob).",
    "highshelf": "High shelf: moves everything above the frequency up or down together (a treble knob).",
    "lowpass": "Low-pass: removes everything above the frequency. Gain is ignored.",
    "highpass": "High-pass: removes everything below the frequency (a rumble filter). Gain is ignored.",
    "notch": "Notch: a deep, narrow cut, for killing one whining or ringing tone. Gain is ignored.",
}


def zone_for(freq):
    for lo, hi, name, what in ZONES:
        if lo <= freq < hi:
            return name, what
    return ZONES[-1][2], ZONES[-1][3]


def fmt_hz(f):
    return "%.1f kHz" % (f / 1000.0) if f >= 1000 else "%.0f Hz" % f


def biquad_coeffs(kind, freq, q, gain_db, fs=SAMPLE_RATE):
    """(b0, b1, b2, a0, a1, a2) per the RBJ cookbook."""
    freq = max(1.0, min(freq, fs / 2 - 1))
    q = max(0.01, q)
    w0 = 2 * math.pi * freq / fs
    cos_w0, sin_w0 = math.cos(w0), math.sin(w0)
    alpha = sin_w0 / (2 * q)
    a = 10 ** (gain_db / 40.0)
    if kind == "peaking":
        return 1 + alpha * a, -2 * cos_w0, 1 - alpha * a, 1 + alpha / a, -2 * cos_w0, 1 - alpha / a
    if kind in ("lowshelf", "highshelf"):
        sq = 2 * math.sqrt(a) * alpha
        if kind == "lowshelf":
            return (a * ((a + 1) - (a - 1) * cos_w0 + sq), 2 * a * ((a - 1) - (a + 1) * cos_w0), a * ((a + 1) - (a - 1) * cos_w0 - sq),
                    (a + 1) + (a - 1) * cos_w0 + sq, -2 * ((a - 1) + (a + 1) * cos_w0), (a + 1) + (a - 1) * cos_w0 - sq)
        return (a * ((a + 1) + (a - 1) * cos_w0 + sq), -2 * a * ((a - 1) + (a + 1) * cos_w0), a * ((a + 1) + (a - 1) * cos_w0 - sq),
                (a + 1) - (a - 1) * cos_w0 + sq, 2 * ((a - 1) - (a + 1) * cos_w0), (a + 1) - (a - 1) * cos_w0 - sq)
    if kind == "lowpass":
        return (1 - cos_w0) / 2, 1 - cos_w0, (1 - cos_w0) / 2, 1 + alpha, -2 * cos_w0, 1 - alpha
    if kind == "highpass":
        return (1 + cos_w0) / 2, -(1 + cos_w0), (1 + cos_w0) / 2, 1 + alpha, -2 * cos_w0, 1 - alpha
    if kind == "notch":
        return 1, -2 * cos_w0, 1, 1 + alpha, -2 * cos_w0, 1 - alpha
    return 1, 0, 0, 1, 0, 0


def biquad_db(coeffs, freq, fs=SAMPLE_RATE):
    """Magnitude response in dB of one biquad at `freq`."""
    b0, b1, b2, a0, a1, a2 = coeffs
    w = 2 * math.pi * freq / fs
    c1, s1, c2, s2 = math.cos(w), math.sin(w), math.cos(2 * w), math.sin(2 * w)
    num = complex(b0 + b1 * c1 + b2 * c2, -(b1 * s1 + b2 * s2))
    den = complex(a0 + a1 * c1 + a2 * c2, -(a1 * s1 + a2 * s2))
    mag = abs(num) / abs(den) if abs(den) > 1e-12 else 0.0
    return 20 * math.log10(mag) if mag > 1e-9 else -180.0


def log_freqs(n=240):
    lo, hi = math.log10(F_MIN), math.log10(F_MAX)
    return [10 ** (lo + (hi - lo) * i / (n - 1)) for i in range(n)]


def response(bands, preamp_db=0.0, freqs=None):
    """Total response in dB of a chain of {type, freq, q, gain} bands at each frequency."""
    freqs = freqs or log_freqs()
    coeffs = [biquad_coeffs(b.get("type", "peaking"), float(b["freq"]), float(b.get("q", 1.0)), float(b.get("gain", 0.0))) for b in bands]
    return [(f, preamp_db + sum(biquad_db(c, f) for c in coeffs)) for f in freqs]


def graphic_bands(gains):
    """The X7's 10-band graphic EQ as parametric bells for drawing."""
    return [{"type": "peaking", "freq": float(hz), "q": GRAPHIC_Q, "gain": float(g)} for (hz, _, _), g in zip(GRAPHIC_BANDS, gains, strict=False)]


def describe_band(band):
    """One line a beginner can read: what this parametric band does and what it affects."""
    kind, freq, q, gain = band.get("type", "peaking"), float(band["freq"]), float(band.get("q", 1.0)), float(band.get("gain", 0.0))
    zone, what = zone_for(freq)
    hz = fmt_hz(freq)
    if kind in ("lowpass", "highpass", "notch"):
        verb = {"lowpass": "removes everything above", "highpass": "removes everything below", "notch": "cuts a very narrow slice at"}[kind]
        return "%s %s · %s" % (verb.capitalize(), hz, what)
    if abs(gain) < 0.05:
        return "Off (0 dB) · %s: %s" % (zone.lower(), what)
    move = "%s %.1f dB" % ("Boosts" if gain > 0 else "Cuts", abs(gain))
    if kind == "lowshelf":
        return "%s everything below %s · %s: %s" % (move, hz, zone.lower(), what)
    if kind == "highshelf":
        return "%s everything above %s · %s: %s" % (move, hz, zone.lower(), what)
    width = "very wide" if q < 0.5 else "wide" if q < 1.0 else "medium" if q < 2.5 else "narrow" if q < 6 else "very narrow"
    return "%s around %s, %s (Q %.2f) · %s: %s" % (move, hz, width, q, zone.lower(), what)


def describe_graphic(index, gain):
    hz, zone, what = GRAPHIC_BANDS[index]
    if abs(gain) < 0.05:
        return "%s · %s" % (zone, what)
    return "%s %.1f dB · %s · %s" % ("Boosting" if gain > 0 else "Cutting", abs(gain), zone, what)
