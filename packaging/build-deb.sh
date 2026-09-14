#!/usr/bin/env bash
# Build the Debian package inside a throwaway container (default debian:trixie).
# The repo is bind-mounted read-only, copied to a scratch dir in the container,
# built with dpkg-buildpackage, checked with lintian, and the results are copied
# to packaging/dist/ owned by the calling user.
#
#   packaging/build-deb.sh                 # debian:trixie
#   DEB_IMAGE=ubuntu:24.04 packaging/build-deb.sh
#   LINTIAN=0 packaging/build-deb.sh       # skip the lintian report
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"
dist="$here/dist"
image="${DEB_IMAGE:-debian:trixie}"
lintian="${LINTIAN:-1}"
mkdir -p "$dist"

docker run --rm -i \
  --volume "$repo:/src:ro" \
  --volume "$dist:/out" \
  --env "HOST_UID=$(id -u)" \
  --env "HOST_GID=$(id -g)" \
  --env "RUN_LINTIAN=$lintian" \
  "$image" bash -s <<'EOF'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update -qq
pkgs=(build-essential debhelper python3 python3-pytest)
if [ "$RUN_LINTIAN" = 1 ]; then pkgs+=(lintian); fi
apt-get install -y -qq --no-install-recommends "${pkgs[@]}" >/dev/null

mkdir -p /tmp/build/x7control
cd /tmp/build/x7control
cp -a /src/Makefile /src/LICENSE /src/pyproject.toml /src/bin /src/data /src/x7control /src/tests .
cp -a /src/packaging/debian debian

dpkg-buildpackage --unsigned-source --unsigned-changes --build=binary

cd /tmp/build
if [ "$RUN_LINTIAN" = 1 ]; then
  echo "== lintian"
  lintian --info --pedantic ./*.changes || true
fi

cp -v ./*.deb ./*.changes ./*.buildinfo /out/
chown "$HOST_UID:$HOST_GID" /out/*
EOF

echo "Built:"
ls -l "$dist"/x7control_*.deb
