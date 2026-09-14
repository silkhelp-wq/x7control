"""x7control-mictest - hear and measure what the voice filter lets through.

  x7control-mictest            record 6 s from the raw mic and the voice-only source at the same
                               time, print noise floor / peak for both, then play back filtered
                               and raw through the X7 (or the default output) so you can compare.
  x7control-mictest -s 10      record 10 s instead.
  x7control-mictest --monitor  live self-monitor: the voice-only mic into your headphones
                               (Ctrl+C to stop). Talk, stop talking, tap the desk, type.
  x7control-mictest --quiet    skip playback (just numbers).
"""
import argparse
import math
import os
import signal
import struct
import subprocess
import sys
import tempfile
import time
import wave

from . import pipewire as pw


def stats(path):
    with wave.open(path) as w:
        n = w.getnframes()
        data = w.readframes(n)
        ch = w.getnchannels()
        rate = w.getframerate()
    samples = struct.unpack("<%dh" % (len(data) // 2), data)[::ch]
    if not samples:
        return None
    win = rate // 10  # 100 ms windows
    rms = []
    for i in range(0, len(samples) - win, win):
        chunk = samples[i:i + win]
        rms.append(math.sqrt(sum(s * s for s in chunk) / len(chunk)) / 32768.0)
    if not rms:
        return None

    def db(v):
        return 20 * math.log10(v) if v > 1e-6 else -120.0
    rms.sort()
    return {"floor": db(rms[len(rms) // 10]), "median": db(rms[len(rms) // 2]), "peak": db(rms[-1]),
            "silent_windows": sum(1 for r in rms if r < 1e-4), "windows": len(rms)}


def record_both(raw, voice, seconds, d, say=print):
    raw_f, voice_f = os.path.join(d, "raw.wav"), os.path.join(d, "voice.wav")
    procs = [subprocess.Popen(["pw-record", "--target", raw, "--rate", "48000", "--channels", "1", "--format", "s16", raw_f]),
             subprocess.Popen(["pw-record", "--target", voice, "--rate", "48000", "--channels", "1", "--format", "s16", voice_f])]
    say("Recording %d s from the raw microphone and the voice filter at the same time." % seconds)
    say("Say a sentence, then stay quiet for a bit, then tap the desk or type something...")
    for left in range(seconds, 0, -1):
        sys.stdout.write("\r  %2d s " % left)
        sys.stdout.flush()
        time.sleep(1)
    say("")
    for p in procs:
        p.send_signal(signal.SIGINT)
    for p in procs:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
    return raw_f, voice_f


def playback_sink():
    n = pw.x7_sink()
    if n:
        return n["name"]
    return pw.defaults().get("sink")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="x7control-mictest", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-s", "--seconds", type=int, default=6, choices=range(2, 61), metavar="2-60")
    ap.add_argument("--monitor", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    raw = pw.voice_conf_source()
    if not raw or not pw.find_node(pw.VOICE_NODE):
        print("The voice filter is not set up or not running. Set it up on the PC page of X7 Control.")
        return 1
    sink = playback_sink()
    if a.monitor:
        if not sink:
            print("No output sink found")
            return 1
        print("Live monitor: voice filter -> %s. Ctrl+C to stop." % sink)
        try:
            subprocess.run(pw.loopback_command(pw.VOICE_NODE, sink))
        except KeyboardInterrupt:
            pass
        return 0
    with tempfile.TemporaryDirectory(prefix="x7control-mictest-") as d:
        raw_f, voice_f = record_both(raw, pw.VOICE_NODE, a.seconds, d)
        rs, vs = stats(raw_f), stats(voice_f)
        if not rs or not vs:
            print("Nothing recorded (check the sources with: wpctl status)")
            return 1
        print("\n                    raw mic     voice-only   (RMS, dBFS; 100 ms windows)")
        print("  noise floor    %8.1f     %8.1f" % (rs["floor"], vs["floor"]))
        print("  median         %8.1f     %8.1f" % (rs["median"], vs["median"]))
        print("  loudest        %8.1f     %8.1f" % (rs["peak"], vs["peak"]))
        print("  fully silent   %5d/%d    %5d/%d windows" % (rs["silent_windows"], rs["windows"], vs["silent_windows"], vs["windows"]))
        print("\nGood result: the voice-only 'loudest' stays near the raw one (your voice passes) while its")
        print("noise floor drops to -100 dB or lower and most quiet windows are fully silent.")
        if not a.quiet and sink:
            for label, f in (("voice-only", voice_f), ("raw mic", raw_f)):
                print("\nPlaying back the %s recording..." % label)
                subprocess.run(["pw-play", "--target", sink, f])
                time.sleep(0.5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
