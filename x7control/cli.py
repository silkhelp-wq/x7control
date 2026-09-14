"""x7ctl - control a Creative Sound Blaster X7 from the terminal over Bluetooth.

  x7ctl pair                 scan for the X7 (put it in pairing mode first: hold Power/Bluetooth 2 s), pair, remember its address
  x7ctl status               output mode, button states, volume, feature switches
  x7ctl headphones           route output to the headphone jacks
  x7ctl speakers             route output to the speaker terminals / line out
  x7ctl stereo | surround51  speaker configuration (implies speakers)
  x7ctl volume [-64..0]      master volume in dB (prints it when no value is given)
  x7ctl mute | unmute
  x7ctl sbx on|off           SBX Pro Studio processing on the box
  x7ctl get MODULE ID...     read DSP parameters (module 150 = playback, 149 = CrystalVoice)
  x7ctl set MODULE ID VALUE  write one DSP parameter (float)
  x7ctl feature NAME on|off  firmware switches: direct, hp_high_gain, hires_usb, auto_sleep, restore_default...
  x7ctl commit               tell the box to save its current settings
  x7ctl usbreset             reset the X7 on the USB bus (needs the udev rule)
  x7ctl raw CMD BYTES...     send an arbitrary frame (hex; e.g. raw 29 01)
Options: --mac XX:XX:XX:XX:XX:XX overrides the saved address.
"""
import argparse
import json
import sys

from . import __version__, bluetooth, config, soundcore as sc, usb


def _hex(b):
    return " ".join("%02x" % x for x in b)


def _onoff(s):
    return str(s).lower() in ("on", "1", "true", "yes")


def _status(c):
    spk = c.get_speaker_config()
    print("output:   %s (%d)" % (sc.SPK_NAMES.get(spk, "unknown"), spk))
    btn = c.get_buttons()
    print("buttons:  " + ", ".join("%s=%s" % (sc.BTN_NAMES[b], "on" if v else "off") for b, v in btn.items()))
    print("volume:   %.1f dB" % c.get_volume_db())
    en, av = c.get_features()
    names = [n for n, o in sc.FEATURES.items() if en >> o & 1]
    print("features: %s (available mask 0x%08x)" % (", ".join(names) or "none", av))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="x7ctl", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mac", help="Bluetooth address of the X7 (overrides the saved one)")
    ap.add_argument("--version", action="version", version="x7ctl %s" % __version__)
    ap.add_argument("cmd", nargs="?", default="status")
    ap.add_argument("args", nargs="*")
    a = ap.parse_args(argv)

    if a.cmd == "pair":
        try:
            mac = bluetooth.pair(status=print)
        except RuntimeError as e:
            print(e)
            return 1
        cfg = config.load()
        cfg["mac"] = mac
        config.save(cfg)
        print("Saved. Try: x7ctl status")
        return 0
    if a.cmd == "usbreset":
        try:
            print(usb.reset())
            return 0
        except OSError as e:
            print("USB reset failed: %s (is the udev rule installed?)" % e)
            return 1

    mac = a.mac or config.load().get("mac")
    if not mac:
        mac, _ = bluetooth.find_x7()
    if not sc.valid_mac(mac):
        print("No X7 address known. Run: x7ctl pair")
        return 1
    c = sc.X7Client(mac)
    try:
        c.connect()
    except sc.X7Error as e:
        print("Could not open the control channel to %s: %s" % (mac, e))
        return 1
    try:
        return _dispatch(c, a, ap)
    except sc.X7Error as e:
        print("error: %s" % e)
        return 1
    except (IndexError, ValueError, KeyError):
        ap.print_help()
        return 2
    finally:
        c.close()


def _dispatch(c, a, ap):
    cmd, args = a.cmd, a.args
    if cmd == "status":
        _status(c)
    elif cmd == "headphones":
        c.set_speaker_config(sc.SPK_HEADPHONES); _status(c)
    elif cmd == "speakers":
        c.set_speaker_config(sc.SPK_TOGGLE_TO_SPEAKER); _status(c)
    elif cmd == "stereo":
        c.set_speaker_config(sc.SPK_STEREO); c.set_speaker_config(sc.SPK_TOGGLE_TO_SPEAKER); _status(c)
    elif cmd == "surround51":
        c.set_speaker_config(sc.SPK_SURROUND_51); c.set_speaker_config(sc.SPK_TOGGLE_TO_SPEAKER); _status(c)
    elif cmd == "volume":
        if args:
            c.set_volume_db(float(args[0]))
        print("volume: %.1f dB" % c.get_volume_db())
    elif cmd == "mute":
        c.set_button(sc.BTN_MUTE, True)
    elif cmd == "unmute":
        c.set_button(sc.BTN_MUTE, False)
    elif cmd == "sbx":
        c.set_button(sc.BTN_SBX, _onoff(args[0]))
    elif cmd == "get":
        module = int(args[0])
        vals = c.get_params(module, [int(x) for x in args[1:]])
        print(json.dumps({str(k): round(v, 4) for k, v in vals.items()}, indent=2))
    elif cmd == "set":
        c.set_param(int(args[0]), int(args[1]), float(args[2]))
        print("ok")
    elif cmd == "commit":
        c.commit(); print("saved")
    elif cmd == "feature":
        c.set_feature(args[0], _onoff(args[1])); c.commit(); _status(c)
    elif cmd == "raw":
        b = bytes(int(h, 16) for h in args)
        rp = c.request(b[0], b[1:], timeout=1.0)
        print("<- %s" % _hex(rp))
    else:
        ap.print_help()
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
