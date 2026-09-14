---
title: X7 Control – Sound Blaster X7 on Linux
description: A free, open-source Linux app for the Creative Sound Blaster X7. Controls the X7 over Bluetooth (output, volume, SBX, EQ, CrystalVoice) and adds a PipeWire headphone EQ with AutoEq import, HRTF game surround and an RNNoise voice filter.
---

# X7 Control

**The Sound Blaster X7 on Linux, fully controlled.** Creative's phone app for the X7 is gone and
there was never a Linux one. X7 Control talks to the X7 over Bluetooth with the same protocol
the app used, so every setting inside the box is yours again, and it adds the PC-side audio
tools Windows users had, as PipeWire filters.

[Download the latest release](https://github.com/silkhelp-wq/x7control/releases/latest) ·
[Source code on GitHub](https://github.com/silkhelp-wq/x7control) ·
[Report a problem](https://github.com/silkhelp-wq/x7control/issues)

![Headphone EQ page with the live response graph](screenshot-eq.png)

## On the X7 itself

- Output routing: headphones, stereo speakers, 5.1 speakers
- Master volume in dB and the knob-press mute (the usual reason an X7 "goes silent")
- SBX Pro Studio: Surround, Crystalizer, Bass, Smart Volume, Dialog Plus
- The X7's own 10-band graphic EQ with presets and a live response graph
- CrystalVoice: noise reduction, voice focus, mic smart volume, echo cancellation, mic EQ, voice FX
- Firmware switches: direct mode, headphone high gain, SPDIF-in direct, hi-res USB, auto sleep

## On the PC (PipeWire)

- Headphone correction EQ with **AutoEq import**, a live frequency-response graph and
  plain-language explanations for people new to equalizers
- Optional **7.1 HRTF game surround** sink
- Optional **voice-only microphone** (RNNoise) that gates out keyboard, fans and room noise
- `x7ctl`, a small command-line tool for scripts and keybindings

## Install

Packages on the [release page](https://github.com/silkhelp-wq/x7control/releases/latest):
`.deb` for Debian 13 / Ubuntu 24.04 and newer, `.rpm` for Fedora 40 and newer, `.pkg.tar.zst`
for Arch, CachyOS and Manjaro. Anything else: `sudo make install` from the source tarball.

Then hold the X7's Power/Bluetooth button until it blinks blue and press **Pair…** in the app.

![Device page](screenshot-device.png)

## Privacy and security

No network access, no telemetry, no accounts. Runs as your user and writes only under
`~/.config`. MIT licensed. Not affiliated with or endorsed by Creative Technology Ltd.
