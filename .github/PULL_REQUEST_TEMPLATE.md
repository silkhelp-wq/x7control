## What and why

<!-- One or two sentences. Link the issue if there is one: "Fixes #12". -->

## How I tested it

- Distro and version:
- Tested with a real Sound Blaster X7? (yes over Bluetooth / yes over USB / no, tests only)

## Checklist

- [ ] `ruff check x7control tests` passes
- [ ] `python3 -m pytest -q tests` passes
- [ ] No new runtime dependencies (or they were discussed in an issue first)
- [ ] Anything that builds a subprocess argv, writes a PipeWire `.conf`, or parses bytes from the X7 has a test
- [ ] README / CHANGELOG updated if behaviour changed
