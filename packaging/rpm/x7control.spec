%global appid io.github.silkhelp_wq.X7Control

Name:           x7control
Version:        0.3.0
Release:        1%{?dist}
Summary:        Sound Blaster X7 settings, PipeWire headphone EQ and voice filter
License:        MIT
URL:            https://github.com/silkhelp-wq/x7control
# Release tarball, or the same layout produced by `git archive --prefix=x7control-VERSION/`
Source0:        %{url}/archive/refs/tags/v%{version}/%{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  make
BuildRequires:  python3-devel
BuildRequires:  python3-pytest
BuildRequires:  desktop-file-utils
BuildRequires:  libappstream-glib
BuildRequires:  systemd-rpm-macros

Requires:       python3 >= 3.10
Requires:       python3-gobject
Requires:       python3-cairo
Requires:       gtk4
Requires:       libadwaita >= 1.5
Requires:       pipewire
# pw-cli, pw-dump, pw-record, pw-loopback live in pipewire-utils on Fedora
Requires:       pipewire-utils
Requires:       wireplumber
Requires:       bluez
Requires:       hicolor-icon-theme
# HRTF data file for the game-surround sink (/usr/share/libmysofa/default.sofa)
Recommends:     mysofa

%description
X7 Control talks to a Creative Sound Blaster X7 over Bluetooth and exposes
every setting the discontinued phone app had: output routing, master volume
and mute, SBX Pro Studio effects, the box's 10-band equalizer, CrystalVoice
microphone processing and the firmware switches.

On the PC side it manages a PipeWire headphone-correction EQ (with AutoEq
import), an optional HRTF 7.1 game-surround sink and an RNNoise voice-only
microphone filter.

The game-surround sink needs an HRTF file (the mysofa package ships one). The
voice filter needs the RNNoise LADSPA plugin (librnnoise_ladspa.so) from the
noise-suppression-for-voice project, which Fedora does not package; the app
also looks for it in ~/.local/lib/ladspa.

Not affiliated with or endorsed by Creative Technology Ltd.

%prep
%autosetup

%build
# Pure Python, nothing to build.

%install
%make_install PREFIX=%{_prefix}

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/%{appid}.desktop
appstream-util validate-relax --nonet %{buildroot}%{_metainfodir}/%{appid}.metainfo.xml
%{python3} -m pytest -q tests

%files
%license LICENSE
%{_bindir}/x7control
%{_bindir}/x7ctl
%{_bindir}/x7control-mictest
%{_datadir}/%{name}/
%{_datadir}/applications/%{appid}.desktop
%{_metainfodir}/%{appid}.metainfo.xml
%{_datadir}/icons/hicolor/*/apps/%{appid}.png
%{_datadir}/icons/hicolor/scalable/apps/%{appid}.svg
%{_udevrulesdir}/70-sound-blaster-x7.rules

%changelog
* Mon Sep 14 2026 silkhelp-wq <silkhelp@gmail.com> - 0.3.0-1
- Outputs page: every PipeWire output gets a friendly name, volume, a per-output parametric EQ with a live curve, format/rate/dither/suspend settings and a digital-volume tip; voice filter moved to the Mic page

* Mon Sep 14 2026 silkhelp-wq <silkhelp@gmail.com> - 0.2.0-1
- Live frequency-response graphs, per-band explanations and an equalizer primer on both EQ pages
- Requires python3-cairo for the graph

* Mon Sep 14 2026 silkhelp-wq <silkhelp@gmail.com> - 0.1.0-1
- Initial package.
