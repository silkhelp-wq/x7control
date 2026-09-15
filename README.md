# X7 Control – Sound Blaster X7 on Linux

Control a **Creative Sound Blaster X7** from Linux: a replacement for the discontinued
"Sound Blaster X7 Control" phone app. It talks to the X7 over Bluetooth using the same
protocol the app used, so every setting in the box is reachable (output, SBX, EQ,
CrystalVoice, firmware switches), and it adds the PC-side pieces (headphone EQ, game
surround, voice-only microphone) as PipeWire filters. Works on Debian, Ubuntu, Fedora, Arch
and anything else with PipeWire and GTK 4.

| Device page (talking to the X7) | Parametric EQ with the live response graph | Outputs page (any PipeWire output) |
|---|---|---|
| ![Device page](docs/screenshot-device.png) | ![Equalizer](docs/screenshot-eq.png) | ![Outputs](docs/screenshot-outputs.png) |

Not affiliated with or endorsed by Creative Technology Ltd.

## What it does

**On the X7 itself (over Bluetooth)**

- Output routing: headphones, stereo speakers, 5.1 speakers
- Master volume in dB, and the knob-press mute (the usual reason an X7 "goes silent")
- SBX Pro Studio: Surround, Crystalizer, Bass with crossover, Smart Volume, Dialog Plus
- The X7's own 10-band graphic EQ with preamp, plus saved presets
- CrystalVoice: noise reduction, voice focus, mic smart volume, echo cancellation, mic EQ, voice FX
- Firmware switches: direct mode, headphone high gain, SPDIF-in direct, hi-res USB, auto sleep
- Save to the box (it auto-saves 3 s after a change, like the phone app), factory restore, USB reset

**On the PC (PipeWire / WirePlumber)** — not just for the X7

- **Outputs page**: every output on the machine (the X7, an optical DAC feeding studio monitors,
  the TV over HDMI…) as a card with a friendly name shown across the desktop, volume, mute and
  a one-click "use this output"
- **Per-output parametric EQ**: 10 bands inserted transparently in front of that output by
  WirePlumber, so headphones and speakers each keep their own correction. Live editing,
  presets, **AutoEq import** for headphones and **REW import** for speaker/room correction
- **Format settings per output**: sample format, fixed rate, dither, never-suspend, and a
  warning when a digital link runs at low software volume or 16 bit
- Every equalizer draws its **frequency response live**, every band explains in plain words
  what it changes, and a short "New to equalizers?" primer is built in
- Optional **game surround** sink: virtual 7.1 rendered binaurally with an HRTF (needs libmysofa)
- Optional **voice filter**: RNNoise voice-only microphone with a tunable gate, live monitor and
  a record-and-compare test (needs the RNNoise LADSPA plugin)

Plus `x7ctl`, a small CLI for scripts and keybindings.

## Install

Packages are attached to each [release](https://github.com/silkhelp-wq/x7control/releases).

| Distro | How |
|---|---|
| Debian 13+, Ubuntu 24.04+ | `sudo apt install ./x7control_*.deb` |
| Fedora 40+ | `sudo dnf install ./x7control-*.rpm` |
| Arch / CachyOS / Manjaro | `sudo pacman -U ./x7control-*.pkg.tar.zst` (or build it yourself: `cd packaging/arch && makepkg -si`) |
| Anything else | `sudo make install` (or `make install-user` for `~/.local`) |

The `.tar.gz` on the release page is the source code, not an installer.

Runtime requirements: Python 3.10+, PyGObject, GTK 4.10+, libadwaita 1.5+, PipeWire with
WirePlumber, BlueZ. Optional: `noise-suppression-for-voice` (voice filter), `libmysofa` (game
surround). Older releases such as Debian 12 or Ubuntu 22.04 ship a libadwaita that is too old.

The **USB reset** button needs the udev rule the packages install
(`/usr/lib/udev/rules.d/70-sound-blaster-x7.rules`). Everything else runs as your user with no
special permissions.

## First run

1. Put the X7 in pairing mode: hold its Power/Bluetooth button about 2 seconds until it blinks blue.
2. Open X7 Control, press **Pair…** on the Device page.
3. Done. The app remembers the address, connects on start and reconnects if the box drops the link.

The X7 is paired but left *untrusted* on purpose, so BlueZ never grabs it as a Bluetooth speaker
behind your back. USB audio keeps working as before; Bluetooth is only the control channel.

On the **Outputs** page each output's equalizer, the game surround sink and (on the Mic page) the
voice filter have a **Set up** button that writes one file under `~/.config/pipewire/` and
restarts PipeWire. **Remove** deletes it again. Names and format settings go into one
WirePlumber rules file. Nothing outside `~/.config` is touched.

## The X7 went silent

Push the volume knob once. The knob is the X7's mute button, and the firmware reports "not muted"
even when it is. If that does not help: hold SBX, then hold Power/Bluetooth until three LEDs blink
(factory reset), push the knob again, then **Pair…** again, since the reset drops the bond.

## CLI

```
x7ctl status
x7ctl headphones | speakers | stereo | surround51
x7ctl volume -20
x7ctl mute | unmute
x7ctl sbx on|off
x7ctl feature direct on
x7ctl get 150 57 58        # read DSP params (module 150 playback, 149 CrystalVoice)
x7ctl commit               # save into the box
x7control --diagnose       # what to paste into a bug report
x7control-mictest          # record raw vs. voice-filtered, print levels, play both back
```

## How it works

The X7 speaks Creative's "SoundCore" protocol over Bluetooth RFCOMM channel 1: frames of
`0x5A, command, length, payload`. The parameter map (which DSP module and id is which slider)
was decoded from the Android app; it lives in [`x7control/soundcore.py`](x7control/soundcore.py)
and is the place to look if you want to add something. Prior work that made this possible:
[Sayrus/x7-bluetooth-control](https://github.com/Sayrus/x7-bluetooth-control) and
[dixonte/StreamDeckX7Plugin](https://github.com/dixonte/StreamDeckX7Plugin).

The PC-side features are plain PipeWire `filter-chain` modules; the EQ uses WirePlumber's
`filter.smart` so applications keep targeting the X7 directly and your volume keeps controlling the
hardware.

## Security and privacy

- No network access at all. No telemetry, no update checks, no accounts.
- Runs as your user; writes only `~/.config/x7control/` and `~/.config/pipewire/pipewire.conf.d/`.
- Bytes from the X7 and the contents of the config file are treated as untrusted input and
  validated before use. Every subprocess is called with a fixed argument list, never a shell.
- See [SECURITY.md](SECURITY.md) for reporting a vulnerability.

## Contributing

Bug reports, testing on other distros and X7 firmware versions, and pull requests are welcome.
See [CONTRIBUTING.md](CONTRIBUTING.md). `main` is protected: changes land through reviewed pull
requests.

## License

[MIT](LICENSE).
