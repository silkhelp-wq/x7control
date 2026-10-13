# Changelog

All notable changes to X7 Control are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.1] - 2026-10-12

### Fixed

- Restoring factory defaults no longer floods the main loop with refreshes (the done callback
  returned a timer id, which GLib took as "call me again").
- Settings changed in the last 3 s before quitting are now written to the X7: the commit used to
  be queued on the worker after the connection had already been closed.
- Output volume and mute follow the output's current PipeWire node after a re-enumeration
  instead of the node id captured when the card was built.
- A failed settings save shows a toast instead of a silent traceback, and never leaves a
  partial temp file behind.
- PipeWire restart no longer reports failure on systems without a pipewire-pulse unit.
- The Outputs page reuses one `pw-dump` per poll instead of one extra per output (less stutter).
- Output labels that clean down to nothing are no longer renamed "preset"; a 0 % voice-filter
  threshold or 0 ms grace is honoured instead of silently becoming the default.
- Per-output name and format rules match the X7 by card name, like its EQ already did, so they
  survive the X7's serial-number change.
- Bluetooth writes use `sendall`; `bluetoothctl` scan and `pw-loopback` monitor processes are
  reaped; deleting or saving an EQ preset refreshes every open editor; the Save button no
  longer leaves a second commit pending; a missing `bluetoothctl` and an invalid `--mac` give
  clear messages.

### Changed

- Protocol map: AEC delay, silencer, VoiceFX voice morphing (three formants, pitch and
  envelope modulation, pitch variability, quiver and contour depth) and the mic EQ centre
  frequency and width parameters are now named in `soundcore.py`. Names come from the parameter
  table inside Creative's Windows Control Panel (`SndCrUSB.DLL`, package SBX7_PCDRV_L9_1_02_03)
  and were confirmed against the device with `x7ctl get`.
- Arch package depends on `pipewire-audio`, which owns `pw-record` and `pw-loopback`.
- 0.3.0's release date corrected to 2026-09-18. Dependabot removed; GitHub Actions pins refreshed.

## [0.3.0] - 2026-09-18

### Added

- **Outputs page**, replacing the PC page: one card per PipeWire output (X7, optical/S/PDIF
  DACs, HDMI, anything else) with a friendly name shown across the desktop, volume and mute,
  a compact live response curve, and its own 10-band parametric EQ (opened in a dialog; REW
  and AutoEq import) so speakers and headphones each keep their correction.
- Per-output Advanced settings written as WirePlumber rules: sample format, fixed sample
  rate, dither and never-suspend, plus the format the device is currently running at.
- A tip when a digital output (optical/HDMI) runs at low software volume or 16 bit.
- `x7control --diagnose` lists every sink with its running format.

### Changed

- The PipeWire voice filter moved to the Mic page next to CrystalVoice.
- The X7 headphone EQ is now the X7 card's equalizer on the Outputs page; its config file and
  node names are unchanged.

## [0.2.0] - 2026-09-14

### Added

- Live frequency-response graph on the X7 EQ and PC EQ pages: hover to read the exact
  change at any pitch, listening regions labelled along the top, a dot per active band.
- Every band explains itself in plain language as you move it ("Cuts 2.1 dB everything
  below 105 Hz · bass: kick drum thump, bass guitar weight").
- "New to equalizers?" section on both pages: what the graph shows, small moves, cut
  before you boost, what each frequency region affects, and what type / frequency / Q /
  gain mean on the parametric EQ.

### Changed

- The cairo Python binding is now a dependency (python3-gi-cairo / python-cairo / python3-cairo).

## [0.1.0] - 2026-09-14

First public release.

### Added

- GTK4 / libadwaita app (`x7control`) that controls a Creative Sound Blaster X7 over
  Bluetooth using the SoundCore protocol decoded from Creative's discontinued phone app.
- Device page: headphone / speaker output routing, stereo and 5.1 speaker configuration,
  master volume and mute, SBX master switch and the firmware feature switches
  (direct mode, headphone high gain, hi-res USB, auto sleep, ...).
- SBX Pro Studio page: Surround, Crystalizer, Bass, Smart Volume and Dialog Plus with
  the same parameters the Creative app exposed.
- Box EQ page: the X7's own 10-band graphic equalizer with saved presets.
- CrystalVoice page: microphone processing (noise reduction, acoustic echo cancellation,
  focus, FX) on the box.
- PC page: PipeWire headphone-correction parametric EQ with AutoEq import and live
  bypass, an optional HRTF 7.1 game-surround sink (libmysofa), and an RNNoise voice-only
  microphone filter with adjustable threshold and grace time.
- `x7ctl` command-line tool: pair, status, output routing, volume, SBX, raw DSP
  parameter get/set, firmware features, commit, USB bus reset and raw frames.
- `x7control-mictest`: records the raw and filtered microphone side by side, prints
  noise floor and peak, and plays both back for comparison; live self-monitor mode.
- `x7control --diagnose` prints everything a bug report needs (versions, X7 presence
  over USB and Bluetooth, PipeWire nodes) and nothing private.
- Pairing flow that drops stale bonds and leaves the X7 untrusted in BlueZ so its A2DP
  profile never auto-connects.
- udev rule granting the logged-in user access to the X7's USB node for `x7ctl usbreset`.
- Config file and every byte from the X7 are validated as untrusted input; PipeWire
  drop-in configs and the config file are written atomically under `~/.config`.
- Desktop entry, AppStream metadata, hicolor icons, `Makefile` install targets
  (`install`, `install-user`, `uninstall`, `check`), and Debian, RPM and Arch packaging.

[Unreleased]: https://github.com/silkhelp-wq/x7control/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/silkhelp-wq/x7control/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/silkhelp-wq/x7control/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/silkhelp-wq/x7control/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/silkhelp-wq/x7control/releases/tag/v0.1.0
