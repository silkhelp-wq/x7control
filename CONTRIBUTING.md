# Contributing to X7 Control

Thanks for helping. X7 Control is a small project with one maintainer, so the rules
below exist to keep review quick and the app safe, not to add ceremony.

## Running from a checkout

There is no build step. Install the runtime dependencies from your distro, then run the
package straight out of the source tree:

```sh
git clone https://github.com/silkhelp-wq/x7control.git
cd x7control
PYTHONPATH=. python3 -m x7control            # the GTK app
PYTHONPATH=. python3 -m x7control.cli status  # x7ctl
PYTHONPATH=. python3 -m x7control.mictest     # x7control-mictest
```

Runtime dependencies (distro packages, nothing is pip-installed):

| What | Debian / Ubuntu | Fedora | Arch |
| --- | --- | --- | --- |
| Python >= 3.10 with PyGObject | `python3-gi` | `python3-gobject` | `python-gobject` |
| GTK 4 >= 4.10 | `gir1.2-gtk-4.0` | `gtk4` | `gtk4` |
| libadwaita >= 1.5 | `gir1.2-adw-1` | `libadwaita` | `libadwaita` |
| PipeWire + WirePlumber (`pw-cli`, `pw-dump`, `wpctl`) | `pipewire`, `wireplumber` | `pipewire`, `wireplumber` | `pipewire`, `wireplumber` |
| BlueZ (`bluetoothctl`) | `bluez` | `bluez` | `bluez-utils` |

Optional: `noise-suppression-for-voice` (RNNoise voice filter) and `libmysofa`
(HRTF game surround). `x7control --diagnose` prints what it found and is the first thing
to check when something does not work.

You do not need an X7 to work on most of the code: the tests run without hardware and
`x7control --smoke` opens the window and quits after two seconds even when PipeWire and
Bluetooth are absent (that is exactly what CI does).

## Lint and tests

```sh
pip install ruff pytest         # or your distro's ruff / python3-pytest packages
ruff check x7control tests      # make lint
python3 -m pytest -q tests      # make test
make check                      # lint + tests + desktop-file-validate + appstreamcli validate
```

`make check` needs `desktop-file-utils` and `appstream` (`appstreamcli`) installed.
CI runs the same commands on Ubuntu 24.04 plus a ShellCheck pass over `bin/*.in` and any
`packaging/*.sh`, and the `--smoke` run under Xvfb. Ruff's configuration lives in
`pyproject.toml`; please do not add per-line ignores without a comment saying why.

## Pull request flow

1. Fork the repository and create a branch from `main` (`git switch -c fix-eq-preamp`).
2. Make the change, run `make check`, commit.
3. Open a pull request against `main` and fill in the template.

`main` is protected: every PR needs a review from the maintainer (`@silkhelp-wq`, see
`.github/CODEOWNERS`) and a green CI run before it can merge. If this is your first
contribution to the repository, a maintainer has to approve the CI run before it starts;
that is a GitHub default for new contributors, not a judgement on your change.

Please:

- **Keep PRs small.** One fix or one feature per PR. A 40-line PR gets reviewed the same
  day; a 900-line one waits. Split refactors from behaviour changes.
- **No new runtime dependencies without discussion.** Open an issue first. The app is
  deliberately plain Python on top of what the distro already ships (PyGObject, GTK,
  libadwaita, PipeWire, BlueZ), so packaging stays trivial for Debian, Fedora and Arch.
  Adding a pip-only dependency is very unlikely to be accepted.
- **Explain the why** in the PR description, and say how you tested it: which distro,
  and whether you tried it against a real X7 (over Bluetooth, USB, or not at all).

## Security-sensitive areas need tests

The app runs as your user and shells out to system tools, writes PipeWire configuration
into `~/.config/pipewire`, and parses whatever a Bluetooth device sends it. Changes that
touch any of the following must come with tests in `tests/`:

- **Anything that builds a subprocess argv** (`pipewire.py`, `bluetooth.py`, `mictest.py`,
  `app.py`): argument lists only, never `shell=True`, and every value that came from the
  user or the device is validated (`config.valid_node_name`, `soundcore.valid_mac`, the
  clamps in `config.py`) before it reaches an argv.
- **Anything that writes a PipeWire `.conf`** (`pipewire.eq_conf_text`,
  `surround_conf_text`, `voice_conf_text` and their `write_*` callers): the written text is
  the thing to test, and anything interpolated into it must be quoted or validated so a
  preset name cannot break out of a string.
- **Anything that parses bytes from the X7** (`soundcore.py`): frames are untrusted input.
  Length-check before `struct.unpack`, keep the receive buffer bounded, and add a test
  that feeds the parser truncated, oversized and junk-prefixed data
  (see `tests/test_soundcore.py` for the pattern).

`config.load()` must keep surviving a garbage or hostile `config.json`; see
`tests/test_config.py`.

## Commit style

- Short imperative subject line (about 50 characters, no trailing period):
  `Clamp box EQ gains before sending them`, not `Fixed EQ bug`.
- A blank line, then a body explaining *why* when the diff does not make that obvious:
  what was wrong, what the X7 or PipeWire actually does, links to any protocol notes.
- Reference issues in the body (`Fixes #12`), not in the subject.
- One logical change per commit; it is fine to squash fixups before opening the PR.
- Add a line under `## [Unreleased]` in `CHANGELOG.md` for anything a user would notice.

## Protocol notes

The SoundCore frame format and the DSP parameter map were decoded from Creative's
Android app plus live probing of an X7; the module docstrings in `soundcore.py` are the
reference. If you discover a new command or parameter, put the evidence (which app code
path, which bytes on the wire) in the PR so the next person can verify it.

## Licence

By contributing you agree that your changes are released under the MIT licence in
`LICENSE`.
