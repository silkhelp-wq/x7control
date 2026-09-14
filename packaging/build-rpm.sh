#!/usr/bin/env bash
# Build the RPM inside a throwaway container (default fedora:42).
# The repo is bind-mounted read-only, packed into the tarball the spec's
# Source0 expects, built with rpmbuild, checked with rpmlint, and the results
# are copied to packaging/dist/ owned by the calling user.
#
#   packaging/build-rpm.sh
#   RPM_IMAGE=fedora:43 packaging/build-rpm.sh
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"
dist="$here/dist"
image="${RPM_IMAGE:-fedora:42}"
mkdir -p "$dist"

docker run --rm -i \
  --volume "$repo:/src:ro" \
  --volume "$dist:/out" \
  --env "HOST_UID=$(id -u)" \
  --env "HOST_GID=$(id -g)" \
  "$image" bash -s <<'EOF'
set -euo pipefail

dnf -y -q install rpm-build rpmlint make python3-devel python3-pytest desktop-file-utils libappstream-glib systemd-rpm-macros

spec=/src/packaging/rpm/x7control.spec
version="$(sed -n 's/^Version:[[:space:]]*//p' "$spec")"
top=/tmp/build
mkdir -p "$top"/{SOURCES,SPECS,BUILD,RPMS,SRPMS} "$top/tree/x7control-$version"

cd "$top/tree/x7control-$version"
cp -a /src/Makefile /src/LICENSE /src/pyproject.toml /src/bin /src/data /src/x7control /src/tests .
cd "$top/tree"
tar -czf "$top/SOURCES/x7control-$version.tar.gz" "x7control-$version"

rpmbuild --define "_topdir $top" -ba "$spec"

echo "== rpmlint"
rpmlint "$spec" "$top"/RPMS/noarch/*.rpm "$top"/SRPMS/*.rpm || true

cp -v "$top"/RPMS/noarch/*.rpm "$top"/SRPMS/*.rpm /out/
chown "$HOST_UID:$HOST_GID" /out/*
EOF

echo "Built:"
ls -l "$dist"/x7control-*.rpm
