# Security policy

## Reporting a vulnerability

Please report security problems privately, not as a public issue or pull request:

**https://github.com/silkhelp-wq/x7control/security/advisories/new**
("Report a vulnerability" on the repository's Security tab).

Include what you found, how to reproduce it, and what you think the impact is. A proof
of concept helps but is not required. If you would rather not use GitHub's form, open a
minimal public issue that says only "security, please contact me" and the maintainer will
reach out through the advisory workflow.

You will get an acknowledgement within one week. After that the maintainer will confirm
or dispute the finding, agree a disclosure timeline with you (normally a fix first, then
a public advisory and a CHANGELOG entry crediting you unless you prefer otherwise), and
release a fixed version. This is a one-person, spare-time project, so please allow some
slack on the fix itself; the acknowledgement is the part with a deadline.

## Supported versions

Only the latest release is supported. Fixes are shipped as a new release, not backported.

| Version | Supported |
| --- | --- |
| latest release (0.1.x) | yes |
| anything older | no, please upgrade |

## Threat model

Knowing what the app does makes it easier to judge whether something is a vulnerability.

**What it is.** A GTK4 desktop app plus two command-line tools that run as the logged-in
user, with the user's normal privileges. There is no daemon, no setuid component and no
privileged helper.

**What it talks to.** Only things on the local machine:

- the user's own PipeWire / WirePlumber session (`pw-cli`, `pw-dump`, `wpctl`,
  `pw-record`, `pw-loopback`, `pw-play`, and `systemctl --user restart` of the PipeWire
  user services), through subprocesses with fixed argument lists and no shell;
- BlueZ, through `bluetoothctl`, for pairing;
- the Sound Blaster X7 itself, over a bonded Bluetooth RFCOMM link (the SoundCore
  control protocol), and its USB device node for a bus reset.

**What it never does.** It never touches the network. No telemetry, no crash reporting,
no update checks, no auto-update, no downloads. AutoEq presets are imported from a file
the user picks, not fetched.

**Untrusted input.** Two sources are treated as hostile:

- every byte the X7 sends over Bluetooth: frames are length-checked before they are
  unpacked and the receive buffer is bounded, so a malicious or malfunctioning device
  should at worst produce wrong readings, never code execution or a hang;
- the user's own config file (`~/.config/x7control/config.json`): every value is
  validated and clamped on load (names stripped of control characters and markup,
  numbers range-checked, node names matched against a strict pattern) before it is shown
  in the UI, written into a PipeWire config, or placed in a subprocess argument.

**What it writes.** Only under the user's `~/.config`: its own `config.json` and PipeWire
drop-in files in `~/.config/pipewire/pipewire.conf.d/` (headphone EQ, HRTF surround,
voice filter). Writes are atomic (temp file + rename). Nothing is written system-wide.

**The udev rule.** The optional `70-sound-blaster-x7.rules` shipped in `data/udev/`
grants the *logged-in* user (`TAG+="uaccess"`) read/write access to the X7's USB HID
node and USB device node, matched on Creative's vendor/product ID `041e:323a`. It grants
nothing to other users, nothing for other devices, and is only needed for `x7ctl
usbreset`.

**Bluetooth.** After pairing, the X7 is deliberately left *untrusted* in BlueZ so it
cannot auto-connect its A2DP audio profile without the user asking. The RFCOMM control
channel requires an existing bond; the app does not weaken pairing security.

Things that would count as vulnerabilities: anything that lets crafted device data or a
crafted config file execute code, escape a subprocess argument, write outside
`~/.config`, or hang the app; the udev rule matching more than the X7; any code path
that reaches the network. Things that are out of scope: attacks that already require
the attacker to be running as your user, and Bluetooth stack (BlueZ, kernel) or
PipeWire bugs, which should go to those projects.
