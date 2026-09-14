#!/usr/bin/env bash
# Build the Arch package from the local tree with makepkg (no root needed).
# Result: packaging/dist/x7control-<version>-<rel>-any.pkg.tar.zst, plus a refreshed .SRCINFO.
# Extra arguments are passed to makepkg (e.g. --nocheck).
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
dist="$here/dist"
mkdir -p "$dist"

cd "$here/arch"
check=()
if ! pacman -Q python-pytest >/dev/null 2>&1; then
  echo "python-pytest is not installed; skipping the test suite (--nocheck)"
  check=(--nocheck)
fi
PKGDEST="$dist" makepkg --force --cleanbuild "${check[@]}" "$@"
makepkg --printsrcinfo > .SRCINFO
rm -rf pkg src

echo "Built:"
ls -l "$dist"/x7control-*.pkg.tar.*
