# X7 Control - install / uninstall.  Pure Python, no build step.
#
#   make install                       # into /usr/local (needs root)
#   make install PREFIX=/usr DESTDIR=$pkgdir   # from a distro package
#   make install-user                  # into ~/.local, no root
#   make uninstall
#
# Runtime dependencies (not installed by this file): python3 >= 3.10, PyGObject,
# GTK 4 >= 4.10, libadwaita >= 1.5, PipeWire + WirePlumber (pw-cli, pw-dump, wpctl),
# BlueZ (bluetoothctl). Optional: noise-suppression-for-voice (voice filter),
# libmysofa (game surround).

PREFIX   ?= /usr/local
DESTDIR  ?=
BINDIR    = $(DESTDIR)$(PREFIX)/bin
LIBDIR    = $(DESTDIR)$(PREFIX)/share/x7control
DATADIR   = $(DESTDIR)$(PREFIX)/share
UDEVDIR   = $(DESTDIR)$(PREFIX)/lib/udev/rules.d
APP_ID    = io.github.silkhelp_wq.X7Control
PY_FILES  = $(wildcard x7control/*.py)
ICON_SIZES = 16x16 24x24 32x32 48x48 64x64 96x96 128x128 192x192 256x256
PYTHON   ?= python3

.PHONY: all install install-user uninstall check test lint clean

all:
	@echo "Nothing to build. Run: make install  (or make install-user)"

install:
	install -d $(LIBDIR)/x7control $(BINDIR) $(DATADIR)/applications $(DATADIR)/metainfo $(DATADIR)/icons/hicolor/scalable/apps $(UDEVDIR)
	install -m 644 $(PY_FILES) $(LIBDIR)/x7control/
	for b in x7control x7ctl x7control-mictest; do \
	  sed "s|@LIBDIR@|$(PREFIX)/share/x7control|g" bin/$$b.in > $(BINDIR)/$$b && chmod 755 $(BINDIR)/$$b; \
	done
	install -m 644 data/$(APP_ID).desktop $(DATADIR)/applications/
	install -m 644 data/$(APP_ID).metainfo.xml $(DATADIR)/metainfo/
	install -m 644 data/icons/hicolor/scalable/apps/$(APP_ID).svg $(DATADIR)/icons/hicolor/scalable/apps/
	for s in $(ICON_SIZES); do \
	  install -d $(DATADIR)/icons/hicolor/$$s/apps; \
	  install -m 644 data/icons/hicolor/$$s/apps/$(APP_ID).png $(DATADIR)/icons/hicolor/$$s/apps/; \
	done
	install -m 644 data/udev/70-sound-blaster-x7.rules $(UDEVDIR)/

# Per-user install: same layout under ~/.local (udev rule is skipped; see README).
install-user:
	$(MAKE) install PREFIX=$(HOME)/.local UDEVDIR=$(HOME)/.local/share/x7control/udev
	-update-desktop-database $(HOME)/.local/share/applications 2>/dev/null
	-gtk-update-icon-cache -q $(HOME)/.local/share/icons/hicolor 2>/dev/null

uninstall:
	rm -rf $(LIBDIR)
	rm -f $(BINDIR)/x7control $(BINDIR)/x7ctl $(BINDIR)/x7control-mictest
	rm -f $(DATADIR)/applications/$(APP_ID).desktop $(DATADIR)/metainfo/$(APP_ID).metainfo.xml
	rm -f $(DATADIR)/icons/hicolor/scalable/apps/$(APP_ID).svg
	for s in $(ICON_SIZES); do rm -f $(DATADIR)/icons/hicolor/$$s/apps/$(APP_ID).png; done
	rm -f $(UDEVDIR)/70-sound-blaster-x7.rules

lint:
	ruff check x7control tests

test:
	$(PYTHON) -m pytest -q tests

check: lint test
	desktop-file-validate data/$(APP_ID).desktop
	appstreamcli validate --no-net data/$(APP_ID).metainfo.xml

clean:
	rm -rf x7control/__pycache__ tests/__pycache__ .pytest_cache .ruff_cache dist build
