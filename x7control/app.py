"""X7 Control - Sound Blaster X7 settings over Bluetooth plus the PipeWire side of a desk.

Pages: Device (output, volume, SBX master, feature switches), SBX Pro Studio effects,
the X7's own 10-band graphic EQ, Mic (CrystalVoice and the PipeWire voice filter), and
Outputs (every PipeWire sink: naming, volume, per-output parametric EQ, format, surround).
"""
import argparse
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading

from . import APP_ID, __version__, bluetooth, config, eqcurve, pipewire as pw, soundcore as sc, usb

COMMIT_DELAY_MS = 3000       # the Creative app commits 3 s after the last change
POLL_SECONDS = 8
FOCUS_ANGLE_OFFSET = 20      # the app shows wedge angle - 20
PAGES = ("device", "sbx", "boxeq", "mic", "outputs")
PAGE_ALIASES = {"pc": "outputs"}
MICTEST_MAX_LINES = 6
DIGITAL_RE = re.compile(r"iec958|hdmi|spdif|digital", re.I)


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="x7control", description="Sound Blaster X7 control for Linux")
    ap.add_argument("--version", action="version", version="x7control %s" % __version__)
    ap.add_argument("--page", choices=PAGES + tuple(PAGE_ALIASES), help="open on this page")
    ap.add_argument("--diagnose", action="store_true", help="print environment details for a bug report and exit")
    ap.add_argument("--smoke", action="store_true", help=argparse.SUPPRESS)  # build the window, quit after 2 s (CI)
    return ap.parse_args(argv)


def _mask(text):
    """Hide serial numbers, Bluetooth addresses and the home directory in diagnostic output."""
    text = re.sub(r"_[0-9A-Za-z]{6,}-00\.", "_<serial>-00.", text)
    text = re.sub(r"(?i)bluez_(input|output)\.[0-9A-F]{2}(_[0-9A-F]{2}){5}", r"bluez_\1.<mac>", text)
    text = re.sub(r"(?i)\b[0-9A-F]{2}(:[0-9A-F]{2}){5}\b", "<mac>", text)
    home = os.path.expanduser("~")
    return text.replace(home, "~") if home and home != "/" else text


def diagnose():
    """Everything a bug report needs, nothing private: versions, X7 presence, PipeWire nodes."""
    import io
    import platform
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        _diagnose_raw(platform)
    print(_mask(buf.getvalue()), end="")
    return 0


def _diagnose_raw(platform):
    print("x7control %s, python %s, %s" % (__version__, platform.python_version(), platform.platform()))
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk
        print("gtk %d.%d.%d, libadwaita %d.%d.%d" % (Gtk.get_major_version(), Gtk.get_minor_version(), Gtk.get_micro_version(),
                                                     Adw.get_major_version(), Adw.get_minor_version(), Adw.get_micro_version()))
    except (ImportError, ValueError) as e:
        print("gtk/libadwaita: not usable (%s)" % e)
    for tool in ("pw-cli", "pw-dump", "wpctl", "pw-record", "pw-loopback", "bluetoothctl"):
        print("%-12s %s" % (tool, shutil.which(tool) or "MISSING"))
    try:
        r = subprocess.run(["pw-cli", "--version"], capture_output=True, text=True, timeout=10)
        print("pipewire:    %s" % (r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "unknown"))
    except (OSError, subprocess.TimeoutExpired) as e:
        print("pipewire:    unknown (%s)" % e)
    cfg = config.load()
    print("x7 paired:   %s" % ("yes" if cfg.get("mac") else "no"))
    if cfg.get("mac"):
        print("bluetooth:   %s" % (bluetooth.info(cfg["mac"]) or "bluetoothctl gave no info"))
    dev, _ = usb.find_device()
    print("x7 on usb:   %s" % ("yes" if dev else "no"))
    dump = pw.pw_dump()
    x7 = pw.x7_sink(dump)
    print("x7 sink:     %s" % (x7["name"] if x7 else "not found"))
    for label, name in (("eq filter", pw.EQ_NODE), ("surround", pw.SURROUND_NODE), ("voice", pw.VOICE_NODE)):
        print("%-12s %s" % (label + ":", "running" if pw.find_node(name, dump) else "not running"))
    print("eq conf:     %s" % ("present" if pw.eq_conf_present() else "absent"))
    print("rnnoise:     %s" % (pw.find_rnnoise() or "not found"))
    print("sofa:        %s" % (pw.find_sofa() or "not found"))
    print("sources:     " + ", ".join(n["name"] for n in pw.sources(dump)))
    for n in pw.sinks(dump):
        fmt, rate = pw.sink_format(n)
        print("sink:        %s (%s %s)" % (n["name"], fmt or "?", rate or "?"))


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.diagnose:
        return diagnose()
    app = build_app(args)
    return app.run([sys.argv[0]])


def build_app(args):
    from . import ui                      # pins the GTK/Adw versions before gi.repository is touched
    from .ui import Adw, ComboRow, EqGraph, Gio, GLib, Gtk, SliderRow, SwitchRow, button_row, help_group

    # ------------------------------------------------------------------ worker
    class Worker:
        """Single background thread for Bluetooth and shell work; results land on the GTK main loop."""

        def __init__(self):
            self.q = queue.Queue()
            threading.Thread(target=self._loop, daemon=True, name="x7-worker").start()

        def _loop(self):
            while True:
                fn, ok, err = self.q.get()
                try:
                    result = fn()
                except Exception as e:  # noqa: BLE001 - reported to the UI as a toast
                    if err:
                        GLib.idle_add(err, e)
                    continue
                if ok:
                    GLib.idle_add(ok, result)

        def run(self, fn, ok=None, err=None):
            self.q.put((fn, ok, err))

    # ------------------------------------------------------------------ window
    class X7Window(Adw.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title="X7 Control", default_width=900, default_height=800)
            self.set_icon_name(APP_ID)
            self.window = self                      # EqEditor host interface
            self.cfg = config.load()
            self.worker = Worker()
            self.client = None
            self.state = {"pb": {}, "voice": {}, "buttons": {}, "features": (0, 0), "speaker": 1, "vol": None}
            self._commit_source = None
            self._poll_source = None
            self._reconnect_source = None
            self._banner_action = None
            self._mic_monitor_proc = None
            self._connecting = False
            self._event_refresh_pending = False
            self.editors = {}                       # slug -> ui.EqEditor
            self.cards = {}                         # sink node name -> card widgets
            self._card_names = None
            self._default_targets = []

            self.toasts = Adw.ToastOverlay()
            self.set_content(self.toasts)
            view = Adw.ToolbarView()
            self.toasts.set_child(view)

            self.stack = Adw.ViewStack()
            header = Adw.HeaderBar()
            switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
            header.set_title_widget(switcher)
            self.status_icon = Gtk.Image.new_from_icon_name("bluetooth-disabled-symbolic")
            self.status_label = Gtk.Label(label="Not connected")
            self.status_label.add_css_class("dim-label")
            sbox = Gtk.Box(spacing=6)
            sbox.append(self.status_icon)
            sbox.append(self.status_label)
            header.pack_start(sbox)
            menu = Gio.Menu()
            menu.append("About X7 Control", "app.about")
            header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu))
            refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
            refresh.set_tooltip_text("Re-read everything from the X7 and PipeWire")
            refresh.connect("clicked", lambda *_: (self.refresh_all(), self.refresh_outputs(force=True), self.refresh_mic_group()))
            header.pack_end(refresh)
            view.add_top_bar(header)
            self.banner = Adw.Banner(revealed=False)
            self.banner.connect("button-clicked", lambda *_: self._banner_action and self._banner_action())
            view.add_top_bar(self.banner)
            view.set_content(self.stack)
            view.add_bottom_bar(Adw.ViewSwitcherBar(stack=self.stack))

            self.stack.add_titled_with_icon(self.build_device_page(), "device", "Device", "audio-card-symbolic")
            self.stack.add_titled_with_icon(self.build_sbx_page(), "sbx", "SBX", "audio-speakers-symbolic")
            self.stack.add_titled_with_icon(self.build_box_eq_page(), "boxeq", "X7 EQ", "media-eq-symbolic")
            self.stack.add_titled_with_icon(self.build_mic_page(), "mic", "Mic", "audio-input-microphone-symbolic")
            self.stack.add_titled_with_icon(self.build_outputs_page(), "outputs", "Outputs", "preferences-desktop-multimedia-symbolic")

            page = args.page or os.environ.get("X7CONTROL_PAGE")
            page = PAGE_ALIASES.get(page, page)
            if page in PAGES:
                self.stack.set_visible_child_name(page)
            self.connect("close-request", self._on_close)
            self.connect_device()
            self.refresh_outputs(force=True)
            self.refresh_mic_group()
            self._poll_source = GLib.timeout_add_seconds(POLL_SECONDS, self._poll)

        # -------------------------------------------------------------- helpers
        def toast(self, text, timeout=3):
            # device names and tool output end up here: never interpret them as markup
            self.toasts.add_toast(Adw.Toast(title=str(text)[:300], timeout=timeout, use_markup=False))

        def save_config(self):
            config.save(self.cfg)

        def show_banner(self, text, button, action):
            self.banner.set_title(text)
            self.banner.set_button_label(button)
            self._banner_action = action
            self.banner.set_revealed(True)

        def set_status(self, connected, text):
            self.status_icon.set_from_icon_name("bluetooth-active-symbolic" if connected else "bluetooth-disabled-symbolic")
            self.status_label.set_text(text)
            self.conn_row.set_subtitle(text)
            self.connect_btn.set_sensitive(not connected)

        def sensitive_device_widgets(self, on):
            for w in self.device_widgets:
                w.set_sensitive(on)

        def confirm(self, *a, **k):
            ui.confirm(self, *a, **k)

        # -------------------------------------------------------------- connection
        def connect_device(self):
            mac = self.cfg.get("mac")
            if not mac:
                self.set_status(False, "No X7 paired yet. Use Pair.")
                self.sensitive_device_widgets(False)
                return
            if self._connecting:
                return
            self._connecting = True
            self.set_status(False, "Connecting to %s…" % mac)

            def do():
                c = sc.X7Client(mac, on_event=self._on_event, on_connection=self._on_connection)
                c.connect()
                return c

            def ok(c):
                self._connecting = False
                old, self.client = self.client, c
                if old is not None and old is not c:
                    old.close()
                self.refresh_all()

            def err(e):
                self._connecting = False
                self.set_status(False, str(e))
                self.sensitive_device_widgets(False)
                self._schedule_reconnect()

            self.worker.run(do, ok, err)

        def _on_connection(self, connected, msg):
            GLib.idle_add(self._connection_changed, connected, msg)

        def _connection_changed(self, connected, msg):
            if connected:
                self.set_status(True, "Connected · %s" % self.cfg.get("mac"))
            else:
                self.client = None
                self.set_status(False, msg)
                self.sensitive_device_widgets(False)
                self._schedule_reconnect()
            return False

        def _schedule_reconnect(self):
            if self._reconnect_source is None:
                self._reconnect_source = GLib.timeout_add_seconds(6, self._reconnect)

        def _reconnect(self):
            self._reconnect_source = None
            if self.client is None:
                self.connect_device()
            return False

        def _on_event(self, cmd, payload):
            # unsolicited frames (front-panel button, knob): re-read the cheap status bits,
            # at most one refresh in flight however many frames the box sends
            if self._event_refresh_pending:
                return
            self._event_refresh_pending = True
            GLib.timeout_add(150, self._event_refresh)

        def _event_refresh(self):
            self._event_refresh_pending = False
            self.refresh_status_bits()
            return False

        def _on_close(self, *_):
            self._stop_mic_monitor()
            if self._commit_source:
                GLib.source_remove(self._commit_source)
                self.commit_now()
            if self.client:
                self.client.close()
            return False

        # -------------------------------------------------------------- device I/O
        def bt(self, fn, ok=None, what="command"):
            """Run fn(client) on the worker; toast on failure."""
            c = self.client
            if not c:
                self.toast("Not connected to the X7")
                return

            def err(e):
                self.toast("X7 %s failed: %s" % (what, e), 5)

            self.worker.run(lambda: fn(c), ok, err)

        def set_playback(self, name, value):
            pid = sc.P[name]
            self.state["pb"][pid] = float(value)
            self.bt(lambda c: c.set_param(sc.MODULE_PLAYBACK, pid, value), what=name)
            self.schedule_commit()

        def set_voice(self, name, value):
            pid = sc.V[name]
            self.state["voice"][pid] = float(value)
            self.bt(lambda c: c.set_param(sc.MODULE_VOICE, pid, value), what=name)
            self.schedule_commit()

        def schedule_commit(self):
            if self._commit_source:
                GLib.source_remove(self._commit_source)
            self._commit_source = GLib.timeout_add(COMMIT_DELAY_MS, self.commit_now)

        def commit_now(self):
            self._commit_source = None
            self.bt(lambda c: c.commit(), what="save")
            return False

        def refresh_all(self):
            if not self.client:
                self.connect_device()
                return

            def do(c):
                pb = c.get_params(sc.MODULE_PLAYBACK, range(0, 63))
                voice = c.get_params(sc.MODULE_VOICE, list(sc.V.values()) + sc.MICEQ_GAIN_IDS)
                return {"pb": pb, "voice": voice, "speaker": c.get_speaker_config(), "buttons": c.get_buttons(),
                        "features": c.get_features(), "vol": c.get_volume_db()}

            def ok(st):
                self.state.update(st)
                self.apply_state()
                self.sensitive_device_widgets(True)

            self.bt(do, ok, "refresh")

        def refresh_status_bits(self):
            def do(c):
                return {"speaker": c.get_speaker_config(), "buttons": c.get_buttons(), "vol": c.get_volume_db()}

            def ok(st):
                self.state.update(st)
                self.apply_status_bits()

            self.bt(do, ok, "status")
            return False

        def _poll(self):
            if self.client and self._commit_source is None:
                self.refresh_status_bits()
            if self.stack.get_visible_child_name() == "outputs":
                self.refresh_outputs()
            return True

        # -------------------------------------------------------------- state -> UI
        def pb(self, name, default=0.0, lo=-1000.0, hi=100000.0):
            # values straight from the box: clamp, and never let NaN/inf reach a widget
            return sc.safe_float(self.state["pb"].get(sc.P[name], default), lo, hi, default)

        def voice(self, name, default=0.0, lo=-1000.0, hi=100000.0):
            return sc.safe_float(self.state["voice"].get(sc.V[name], default), lo, hi, default)

        def apply_status_bits(self):
            spk = self.state["speaker"]
            self.output_row.set_selected_quiet({1: 0, 2: 1, 3: 2, 4: 2}.get(spk, 0))
            btn = self.state["buttons"]
            self.mute_row.set_active_quiet(btn.get(sc.BTN_MUTE, False))
            self.sbx_row.set_active_quiet(btn.get(sc.BTN_SBX, False))
            self.cv_master_row.set_active_quiet(btn.get(sc.BTN_CRYSTALVOICE, False))
            if self.state.get("vol") is not None:
                self.volume_row.set_value_quiet(self.state["vol"])

        def apply_state(self):
            self.apply_status_bits()
            enabled, _avail = self.state["features"]
            for name, row in self.feature_rows.items():
                row.set_active_quiet(enabled >> sc.FEATURES[name] & 1)
            # SBX
            self.surround_sw.set_active_quiet(self.pb("surround_enable"))
            self.surround_lv.set_value_quiet(self.pb("surround_level") * 100)
            self.cryst_sw.set_active_quiet(self.pb("crystalizer_enable"))
            self.cryst_lv.set_value_quiet(self.pb("crystalizer_level") * 100)
            self.bass_sw.set_active_quiet(self.pb("bass_enable"))
            self.bass_lv.set_value_quiet(self.pb("bass_level") * 100)
            self.bass_freq.set_value_quiet(self.pb("bass_freq", 80))
            self.svm_sw.set_active_quiet(self.pb("smartvol_enable"))
            self.svm_lv.set_value_quiet(self.pb("smartvol_level") * 100)
            self.svm_mode.set_selected_quiet(int(self.pb("smartvol_mode", 0, 0, len(sc.SMARTVOL_MODES) - 1)))
            self.dialog_sw.set_active_quiet(self.pb("dialogplus_enable"))
            self.dialog_lv.set_value_quiet(self.pb("dialogplus_level") * 100)
            # box EQ
            self.boxeq_sw.set_active_quiet(self.pb("eq_enable"))
            self.boxeq_pre.set_value_quiet(self.pb("eq_preamp"))
            for i, row in enumerate(self.boxeq_bands):
                row.set_value_quiet(self.pb("eq_band%d" % i))
            self.refresh_box_graph()
            # mic
            self.nr_sw.set_active_quiet(self.voice("nr_enable"))
            self.nr_lv.set_value_quiet(self.voice("nr_level") * 100)
            self.focus_sw.set_active_quiet(self.voice("focus_enable"))
            self.focus_angle.set_value_quiet(max(0, self.voice("focus_wedge_angle", 20) - FOCUS_ANGLE_OFFSET))
            self.micsvm_sw.set_active_quiet(self.voice("micsvm_enable"))
            self.micsvm_lv.set_value_quiet(self.voice("micsvm_level") * 100)
            self.aec_sw.set_active_quiet(self.voice("aec_enable"))
            self.miceq_sw.set_active_quiet(self.voice("miceq_enable"))
            self.fx_sw.set_active_quiet(self.voice("fx_enable"))

        # -------------------------------------------------------------- pages: device / SBX / X7 EQ
        def build_device_page(self):
            page = Adw.PreferencesPage()
            self.device_widgets = []

            g = Adw.PreferencesGroup(title="Connection")
            self.conn_row = Adw.ActionRow(title="Sound Blaster X7", subtitle="Not connected", use_markup=False)
            self.connect_btn = Gtk.Button(label="Connect", valign=Gtk.Align.CENTER)
            self.connect_btn.connect("clicked", lambda *_: self.connect_device())
            pair_btn = Gtk.Button(label="Pair…", valign=Gtk.Align.CENTER)
            pair_btn.connect("clicked", lambda *_: self.pair_dialog())
            self.conn_row.add_suffix(self.connect_btn)
            self.conn_row.add_suffix(pair_btn)
            g.add(self.conn_row)
            page.add(g)

            g = Adw.PreferencesGroup(title="Output")
            self.output_row = ComboRow("Output", ["Headphones", "Speakers (stereo 2.0)", "Speakers (5.1)"],
                                       subtitle="Which jacks the X7 plays through", on_select=self.on_output)
            self.volume_row = SliderRow("Master volume", -64, 0, 1, "{:.0f} dB", "Hardware volume, same as the front knob and the USB volume",
                                        on_change=self.on_volume)
            self.mute_row = SwitchRow("Mute", "The knob-press mute; when the X7 goes silent, this is usually why", self.on_mute)
            self.sbx_row = SwitchRow("SBX Pro Studio", "Master switch for all effects on the SBX page (front SBX button)",
                                     lambda v: self.on_button(sc.BTN_SBX, v))
            for r in (self.output_row, self.volume_row, self.mute_row, self.sbx_row):
                g.add(r)
                self.device_widgets.append(r)
            page.add(g)

            g = Adw.PreferencesGroup(title="Device features", description="Firmware switches. Some firmware versions refuse these over Bluetooth; the app tells you when that happens.")
            self.feature_rows = {}
            feats = [("direct", "Direct mode", "Bypass all processing; audio straight from the source"),
                     ("hp_high_gain", "Headphone high gain", "For 600 Ω headphones only. Can damage low-impedance headphones"),
                     ("spdif_in_direct", "SPDIF-In direct", "Bit-perfect optical input, disables other inputs"),
                     ("hires_usb", "Hi-res USB (24-bit/192 kHz)", "Switches the USB interface mode; re-enumerates the device"),
                     ("auto_sleep", "Auto sleep", "Power down after a period of silence"),
                     ("wideband_bt", "Wideband Bluetooth voice", "HD voice on Bluetooth calls")]
            for key, title, sub in feats:
                row = SwitchRow(title, sub, lambda v, k=key: self.on_feature(k, v))
                self.feature_rows[key] = row
                g.add(row)
                self.device_widgets.append(row)
            page.add(g)

            g = Adw.PreferencesGroup(title="Maintenance")
            r = button_row("Save settings to the X7", "Settings are saved automatically 3 s after a change; this forces it now", "Save", self.commit_now)
            g.add(r)
            self.device_widgets.append(r)
            r = button_row("Restore factory defaults", "Resets every setting in the box (output mode, SBX, EQ, mic). Volume stays.", "Restore",
                           self.restore_defaults, destructive=True)
            g.add(r)
            self.device_widgets.append(r)
            g.add(button_row("Reset the USB link", "Re-enumerates the X7 on USB if PipeWire lost it (needs the udev rule)", "Reset USB", self.usb_reset))
            page.add(g)
            return page

        def build_sbx_page(self):
            page = Adw.PreferencesPage()

            def effect(title, desc, en_key, lv_key):
                g = Adw.PreferencesGroup(title=title, description=desc)
                sw = SwitchRow("Enable", None, lambda v: self.set_playback(en_key, 1.0 if v else 0.0))
                lv = SliderRow("Level", 0, 100, 1, "{:.0f} %", on_change=lambda v: self.set_playback(lv_key, v / 100.0))
                g.add(sw)
                g.add(lv)
                page.add(g)
                return g, sw, lv

            _, self.surround_sw, self.surround_lv = effect("Surround", "Virtual speakers around you (headphones or 2.0)", "surround_enable", "surround_level")
            _, self.cryst_sw, self.cryst_lv = effect("Crystalizer", "Restores dynamics lost to compressed audio", "crystalizer_enable", "crystalizer_level")
            g, self.bass_sw, self.bass_lv = effect("Bass", "Low-frequency enhancement", "bass_enable", "bass_level")
            self.bass_freq = SliderRow("Crossover frequency", 10, 1000, 10, "{:.0f} Hz", on_change=lambda v: self.set_playback("bass_freq", v))
            g.add(self.bass_freq)
            g, self.svm_sw, self.svm_lv = effect("Smart Volume", "Evens out sudden volume changes", "smartvol_enable", "smartvol_level")
            self.svm_mode = ComboRow("Mode", sc.SMARTVOL_MODES, on_select=lambda i: self.set_playback("smartvol_mode", float(i)))
            g.add(self.svm_mode)
            _, self.dialog_sw, self.dialog_lv = effect("Dialog Plus", "Brings voices forward in movies and games", "dialogplus_enable", "dialogplus_level")
            page.add(Adw.PreferencesGroup(description="These only take effect while SBX Pro Studio is switched on (Device page or the front SBX button)."))
            return page

        def build_box_eq_page(self):
            page = Adw.PreferencesPage()
            g = Adw.PreferencesGroup(title="Graphic equalizer in the X7", description="Runs inside the box, so it also applies to Bluetooth and optical input. Needs SBX on.")
            self.boxeq_sw = SwitchRow("Enable", "Flip it off and on to compare with and without",
                                      lambda v: (self.set_playback("eq_enable", 1.0 if v else 0.0), self.refresh_box_graph()))
            g.add(self.boxeq_sw)
            self.boxeq_presets = ComboRow("Preset", self.box_preset_names(), on_select=self.on_box_preset)
            g.add(self.boxeq_presets)
            self.boxeq_pre = SliderRow("Preamp", -12, 12, 0.5, "{:+.1f} dB", "Overall level; lower it by your biggest boost so nothing clips",
                                       on_change=lambda v: (self.set_playback("eq_preamp", v), self.refresh_box_graph()), digits=1)
            g.add(self.boxeq_pre)
            page.add(g)
            g = Adw.PreferencesGroup(title="Response", description="What the equalizer does to the sound, from deep bass on the left to treble on the right.")
            self.box_graph = EqGraph()
            g.add(ui.graph_card(self.box_graph))
            page.add(g)
            g = Adw.PreferencesGroup(title="Bands", description="Each slider raises or lowers one region. The line under each one says what lives there.")
            self.boxeq_bands = []
            for i, (hz, _zone, _what) in enumerate(eqcurve.GRAPHIC_BANDS):
                row = SliderRow(eqcurve.fmt_hz(hz), -24, 24, 0.5, "{:+.1f} dB", eqcurve.describe_graphic(i, 0),
                                on_change=lambda v, i=i: self.on_box_band(i, v), digits=1)
                self.boxeq_bands.append(row)
                g.add(row)
            page.add(g)
            g = Adw.PreferencesGroup(title="Presets")
            g.add(button_row("Save current bands as a preset", None, "Save as…", self.save_box_preset))
            g.add(button_row("Delete the selected user preset", None, "Delete", self.delete_box_preset, destructive=True))
            g.add(button_row("Flatten", "Set every band and the preamp to 0 dB", "Flat", lambda: self.apply_box_preset({"preamp": 0, "bands": [0] * 10})))
            page.add(g)
            page.add(help_group("New to equalizers?", "Short answers to the usual questions.", ui.EQ_BASICS + ui.ZONE_HELP))
            self.refresh_box_graph()
            return page

        def on_box_band(self, i, v):
            self.set_playback("eq_band%d" % i, v)
            self.boxeq_bands[i].set_subtitle(eqcurve.describe_graphic(i, v))
            self.refresh_box_graph()

        def refresh_box_graph(self):
            gains = [r.get_value() for r in self.boxeq_bands]
            for i, r in enumerate(self.boxeq_bands):
                r.set_subtitle(eqcurve.describe_graphic(i, gains[i]))
            self.box_graph.set_bands(eqcurve.graphic_bands(gains), self.boxeq_pre.get_value(), bypass=not self.boxeq_sw.get_active())

        # -------------------------------------------------------------- page: mic
        def build_mic_page(self):
            page = Adw.PreferencesPage()
            g = Adw.PreferencesGroup(title="CrystalVoice (inside the X7)", description="Processing for the X7's own mic input (the front mic jack or the array), not USB microphones.")
            self.cv_master_row = SwitchRow("CrystalVoice master", "Front-panel CrystalVoice button", lambda v: self.on_button(sc.BTN_CRYSTALVOICE, v))
            g.add(self.cv_master_row)
            page.add(g)
            g = Adw.PreferencesGroup(title="Noise Reduction")
            self.nr_sw = SwitchRow("Enable", "Removes steady background noise", lambda v: self.set_voice("nr_enable", 1.0 if v else 0.0))
            self.nr_lv = SliderRow("Strength", 0, 100, 1, "{:.0f} %", on_change=lambda v: self.set_voice("nr_level", v / 100.0))
            g.add(self.nr_sw)
            g.add(self.nr_lv)
            page.add(g)
            g = Adw.PreferencesGroup(title="Voice Focus")
            self.focus_sw = SwitchRow("Enable", "Suppresses sound from outside a wedge in front of the mic array", lambda v: self.set_voice("focus_enable", 1.0 if v else 0.0))
            self.focus_angle = SliderRow("Angle", 0, 180, 5, "{:.0f}°", on_change=lambda v: self.set_voice("focus_wedge_angle", v + FOCUS_ANGLE_OFFSET))
            g.add(self.focus_sw)
            g.add(self.focus_angle)
            page.add(g)
            g = Adw.PreferencesGroup(title="Mic Smart Volume")
            self.micsvm_sw = SwitchRow("Enable", "Keeps your voice level steady", lambda v: self.set_voice("micsvm_enable", 1.0 if v else 0.0))
            self.micsvm_lv = SliderRow("Strength", 0, 100, 1, "{:.0f} %", on_change=lambda v: self.set_voice("micsvm_level", v / 100.0))
            g.add(self.micsvm_sw)
            g.add(self.micsvm_lv)
            page.add(g)
            g = Adw.PreferencesGroup(title="Other")
            self.aec_sw = SwitchRow("Acoustic echo cancellation", "Stops speaker output leaking back into the mic", lambda v: self.set_voice("aec_enable", 1.0 if v else 0.0))
            self.miceq_sw = SwitchRow("Mic equalizer", "The box's 8-band mic EQ (bands keep their stored values)", lambda v: self.set_voice("miceq_enable", 1.0 if v else 0.0))
            self.fx_sw = SwitchRow("Voice FX", "Voice-morphing effect (last stored preset)", lambda v: self.set_voice("fx_enable", 1.0 if v else 0.0))
            g.add(self.aec_sw)
            g.add(self.miceq_sw)
            g.add(self.fx_sw)
            page.add(g)

            g = Adw.PreferencesGroup(title="Voice filter (any microphone, in PipeWire)",
                                     description="RNNoise voice-only microphone: only speech passes, keyboard, fans and room noise are gated out.")
            self.mic_source = ComboRow("Microphone to filter", ["(none found)"], "The raw input the filter wraps")
            self.mic_setup_row = button_row("Voice filter", "", "Set up", self.toggle_voice_conf)
            self.mic_sw = SwitchRow("Use the voice-only mic as the default input", "Off = apps get the raw microphone", self.on_mic_enable)
            self.mic_vad = SliderRow("Voice threshold", 0, 100, 1, "{:.0f} %", "Higher = stricter gate. Lower it if the start of your words gets clipped", on_change=lambda v: self.on_mic_param())
            self.mic_grace = SliderRow("Hold open after speech", 0, 1000, 10, "{:.0f} ms", "How long the gate stays open after you stop talking", on_change=lambda v: self.on_mic_param())
            self.mic_monitor = SwitchRow("Live monitor in headphones", "Hear the filtered mic while you talk, tap the desk, type", self.on_mic_monitor)
            self.mic_test_row = button_row("Record a 6 s test and play it back", "Raw and filtered are recorded together; you hear filtered first, then raw", "Test", self.mic_test)
            for r in (self.mic_source, self.mic_setup_row, self.mic_sw, self.mic_vad, self.mic_grace, self.mic_monitor, self.mic_test_row):
                g.add(r)
            page.add(g)
            self.mic_widgets = [self.mic_sw, self.mic_vad, self.mic_grace, self.mic_monitor, self.mic_test_row]
            return page

        # -------------------------------------------------------------- page: outputs
        def build_outputs_page(self):
            scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
            clamp = Adw.Clamp(maximum_size=1100, tightening_threshold=900)
            self.outputs_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_top=24, margin_bottom=24, margin_start=12, margin_end=12)
            clamp.set_child(self.outputs_box)
            scroller.set_child(clamp)

            g = Adw.PreferencesGroup(title="Where sound goes", description="Every output PipeWire knows about. New apps play on the default; "
                                                                             "per-app choices in your desktop's audio applet still win.")
            self.default_row = ComboRow("Default output", ["(none)"], on_select=self.on_default_sink)
            g.add(self.default_row)
            self.outputs_box.append(g)
            self.cards_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
            self.outputs_box.append(self.cards_box)
            self.outputs_box.append(help_group("About these settings", "What the per-output controls do.", [
                ("Shown as", "The name your desktop shows for this output. Handy when the technical name is 'HD Audio Controller Digital Stereo' "
                             "and what it really is is 'the tube DAC feeding the Behringers'."),
                ("Volume on a digital output", "Optical and HDMI carry numbers, not a voltage: turning the software volume down throws away "
                                                "resolution before the DAC. Keep it near 100 % and set the level on the DAC or amplifier; if you must "
                                                "attenuate in software, run the output at 24 or 32 bit so the lost bits are ones you never had."),
                ("Equalizer", "A parametric EQ in front of this output only. Use it for speaker and room correction (import REW filters) or "
                              "headphone correction (import AutoEq). Each output keeps its own settings."),
                ("Format and sample rate", "Auto lets PipeWire pick. Forcing 24/32-bit costs nothing and helps a digital link; forcing a rate "
                                           "makes PipeWire resample everything to it, which a DAC with a rate display will confirm. 96 kHz is a safe "
                                           "choice for optical; 192 kHz over optical fails on many DACs."),
                ("Dither", "Noise shaping applied when the mix is reduced to 16 bits. 'wannamaker3' is a good default on 16-bit links; irrelevant at 24-bit."),
                ("Never suspend", "PipeWire powers idle outputs down after a few seconds. Some DACs and amps click or swallow the first half-second "
                                  "when they wake; this keeps the link open."),
                ("Game surround (X7 only)", "A virtual 7.1 output rendered binaurally for headphones. Not the default on purpose: it colours music."),
            ]))
            return scroller

        def output_cfg(self, name):
            outs = self.cfg.setdefault("outputs", {})
            if name not in outs:
                outs[name] = config.clean_output({})
            return outs[name]

        def eq_enabled_for(self, name):
            if pw.X7_SINK_RE.fullmatch(name):
                return bool(self.cfg.get("pc_eq_enabled", True))
            return bool(self.output_cfg(name).get("eq_enabled", True))

        def set_eq_enabled_for(self, name, v):
            if pw.X7_SINK_RE.fullmatch(name):
                self.cfg["pc_eq_enabled"] = bool(v)
            else:
                self.output_cfg(name)["eq_enabled"] = bool(v)
            self.save_config()

        @staticmethod
        def slug_of(name):
            return "" if pw.X7_SINK_RE.fullmatch(name) else pw.slug_for(name)

        def label_of(self, sink):
            return self.cfg.get("outputs", {}).get(sink["name"], {}).get("label") or sink["description"] or sink["name"]

        @staticmethod
        def hardware_sinks(dump):
            return [s for s in pw.sinks(dump) if not s["name"].startswith("effect_input.")]

        def refresh_outputs(self, force=False):
            dump = pw.pw_dump()
            sinks = self.hardware_sinks(dump)
            names = [s["name"] for s in sinks]
            surround = pw.find_node(pw.SURROUND_NODE, dump)
            self._default_targets = list(sinks)
            if surround:
                self._default_targets.append({"id": surround["id"], "name": pw.SURROUND_NODE, "description": "Game Surround 7.1 (HRTF)"})
            d = pw.defaults(dump).get("sink")
            sel = next((i for i, s in enumerate(self._default_targets) if s["name"] == d), 0)
            labels = [s["description"] if s["name"] == pw.SURROUND_NODE else self.label_of(s) for s in self._default_targets]
            self.default_row.set_items_quiet(labels or ["(none)"], sel)
            self.default_row.set_sensitive(bool(self._default_targets))
            if force or names != self._card_names:
                self._card_names = names
                self.rebuild_cards(sinks)
            for s in sinks:
                self.update_card(s, dump)

        def rebuild_cards(self, sinks):
            while child := self.cards_box.get_first_child():
                self.cards_box.remove(child)
            self.cards = {}
            for s in sinks:
                self.cards[s["name"]] = self.build_card(s)
                self.cards_box.append(self.cards[s["name"]]["group"])
            if not sinks:
                self.cards_box.append(Adw.PreferencesGroup(description="No outputs found. Is PipeWire running?"))

        def build_card(self, sink):
            name = sink["name"]
            ocfg = self.output_cfg(name)
            is_x7 = pw.X7_SINK_RE.fullmatch(name) is not None
            slug = self.slug_of(name)
            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            top = Adw.PreferencesGroup(title=self.label_of(sink), description=name)
            outer.append(top)
            card = {"group": outer, "title_group": top, "sink": sink, "slug": slug}

            entry = Adw.EntryRow(title="Shown as", text=ocfg.get("label") or "", show_apply_button=True)
            entry.connect("apply", lambda *_: self.on_label(name, entry.get_text()))
            top.add(entry)
            card["use"] = button_row("Use this output", "Make it the default for new apps", "Use", lambda n=name: self.set_default_by_name(n))
            top.add(card["use"])
            card["vol"] = SliderRow("Volume", 0, 100, 1, "{:.0f} %", on_change=lambda v, s=sink: self.worker.run(lambda: pw.set_volume(s["id"], v)))
            card["mute"] = SwitchRow("Mute", None, lambda v, s=sink: self.worker.run(lambda: pw.set_mute(s["id"], v)))
            top.add(card["vol"])
            top.add(card["mute"])
            card["tip"] = Adw.ActionRow(title="Tip: digital output", use_markup=False, visible=False)
            card["tip"].add_prefix(Gtk.Image.new_from_icon_name("dialog-information-symbolic"))
            top.add(card["tip"])

            # equalizer: compact curve, then set up / edit / enable
            card["graph"] = EqGraph(height=130, compact=True)
            gbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            gbox.add_css_class("card")
            for side in ("top", "bottom", "start", "end"):
                getattr(card["graph"], "set_margin_" + side)(6)
            gbox.append(card["graph"])
            outer.append(gbox)
            g = Adw.PreferencesGroup()
            outer.append(g)
            card["eq_row"] = button_row("Equalizer", "", "Set up", lambda n=name: self.toggle_eq(n))
            edit = Gtk.Button(label="Edit…", valign=Gtk.Align.CENTER)
            edit.connect("clicked", lambda *_, n=name: self.open_eq_editor(n))
            card["eq_row"].add_suffix(edit)
            card["eq_edit"] = edit
            g.add(card["eq_row"])
            card["eq_sw"] = SwitchRow("Equalizer enabled", "Off = bypass; flip it to compare", lambda v, n=name: self.on_eq_enable(n, v))
            g.add(card["eq_sw"])
            if is_x7:
                card["surround"] = button_row("Game surround sink", "", "Set up", self.toggle_surround)
                g.add(card["surround"])

            adv = Adw.ExpanderRow(title="Advanced", subtitle="Format, sample rate, dither, suspend", use_markup=False)
            card["format"] = ComboRow("Sample format", ["Auto"] + pw.FORMATS[1:], "Bits per sample sent to the device",
                                      on_select=lambda i, n=name: self.on_adv(n, "format", pw.FORMATS[i]))
            card["rate"] = ComboRow("Sample rate", ["Auto"] + ["%s Hz" % r for r in pw.RATES[1:]], "Fixed rate for this device; PipeWire resamples to it",
                                    on_select=lambda i, n=name: self.on_adv(n, "rate", pw.RATES[i]))
            card["dither"] = ComboRow("Dither", ["Auto"] + pw.DITHERS[1:], "Noise shaping when reducing to 16 bits",
                                      on_select=lambda i, n=name: self.on_adv(n, "dither", pw.DITHERS[i]))
            card["nosusp"] = SwitchRow("Never suspend", "Keep the link open so the DAC or amp does not click when it wakes",
                                       lambda v, n=name: self.on_adv(n, "no_suspend", bool(v)))
            card["running"] = Adw.ActionRow(title="Currently running at", subtitle="", use_markup=False)
            card["running"].add_css_class("property")
            for r in (card["running"], card["format"], card["rate"], card["dither"], card["nosusp"]):
                adv.add_row(r)
            g.add(adv)
            def idx(lst, v):
                return lst.index(v) if v in lst else 0
            card["format"].set_selected_quiet(idx(pw.FORMATS, ocfg.get("format") or ""))
            card["rate"].set_selected_quiet(idx(pw.RATES, str(ocfg.get("rate") or "")))
            card["dither"].set_selected_quiet(idx(pw.DITHERS, ocfg.get("dither") or ""))
            card["nosusp"].set_active_quiet(ocfg.get("no_suspend", False))
            return card

        def update_card(self, sink, dump):
            card = self.cards.get(sink["name"])
            if not card:
                return
            name, slug = sink["name"], card["slug"]
            card["sink"] = sink
            vol, muted = pw.get_volume(sink["id"])
            card["vol"].set_value_quiet(vol)
            card["mute"].set_active_quiet(muted)
            is_default = pw.defaults(dump).get("sink") == name
            card["use"].button.set_sensitive(not is_default)
            card["use"].set_subtitle("This is the default output" if is_default else "Make it the default for new apps")
            fmt, rate = pw.sink_format(sink)
            card["running"].set_subtitle("%s, %s Hz" % (fmt or "unknown", rate or "?"))
            digital = DIGITAL_RE.search(name) is not None
            bits16 = (fmt or "").upper().startswith("S16")
            card["tip"].set_visible(digital and vol < 90)
            card["tip"].set_subtitle("Software volume is %d %% on a digital link%s. Set the level on the DAC or amp instead, or force a 24/32-bit format under Advanced."
                                     % (vol, " running at 16 bit" if bits16 else ""))
            present, running = pw.eq_conf_present(slug), pw.eq_running(slug, dump)
            card["eq_row"].button.set_label("Remove" if present else "Set up")
            card["eq_row"].set_subtitle(("Running" if running else "Configured, restart PipeWire to start it") if present
                                        else "Adds a 10-band parametric EQ between every app and this output")
            card["eq_edit"].set_sensitive(running)
            card["eq_sw"].set_sensitive(running)
            enabled = self.eq_enabled_for(name)
            card["eq_sw"].set_active_quiet(enabled)
            preset = pw.read_live_eq(slug) if running else pw.FLAT_PRESET
            card["graph"].set_bands(preset["bands"], preset["preamp"], bypass=not (running and enabled))
            if "surround" in card:
                spresent, srunning = pw.surround_conf_present(), pw.find_node(pw.SURROUND_NODE, dump) is not None
                card["surround"].button.set_label("Remove" if spresent else "Set up")
                if spresent:
                    card["surround"].set_subtitle("Virtual 7.1 sink rendered with an HRTF into the headphone EQ" + ("" if srunning else " (restart PipeWire to start it)"))
                else:
                    card["surround"].set_subtitle("Adds a virtual 7.1 output for games" if pw.find_sofa() else "Needs a SOFA HRTF file (install libmysofa)")
                card["surround"].button.set_sensitive(spresent or pw.find_sofa() is not None)

        # -- output handlers
        def on_label(self, name, text):
            self.output_cfg(name)["label"] = config.clean_name(text, 60) if text.strip() else ""
            self.save_config()
            self.write_outputs_conf()
            card = self.cards.get(name)
            if card:
                card["title_group"].set_title(self.label_of(card["sink"]))
            self.refresh_outputs()
            self.show_banner("Names and format settings apply when PipeWire restarts.", "Restart PipeWire", self.restart_pipewire)

        def on_adv(self, name, key, value):
            self.output_cfg(name)[key] = value
            self.save_config()
            self.write_outputs_conf()
            self.show_banner("Names and format settings apply when PipeWire restarts.", "Restart PipeWire", self.restart_pipewire)

        def write_outputs_conf(self):
            try:
                pw.write_outputs_conf(self.cfg.get("outputs", {}))
            except OSError as e:
                self.toast("Could not write %s: %s" % (pw.OUTPUTS_CONF, e), 5)

        def _set_default_node(self, node):
            self.worker.run(lambda: pw.set_default(node["id"]),
                            lambda ok: (self.toast("Default output set" if ok else "Could not set default output"), self.refresh_outputs()))

        def set_default_by_name(self, name):
            node = pw.find_node(name)
            if not node:
                self.toast("Output not found")
                return
            self._set_default_node(node)

        def on_default_sink(self, idx):
            if idx < len(self._default_targets):
                self._set_default_node(self._default_targets[idx])

        def toggle_eq(self, name):
            slug = self.slug_of(name)
            if pw.eq_conf_present(slug):
                def go():
                    pw.remove_eq_conf(slug)
                    self.editors.pop(slug, None)
                    self.restart_pipewire()
                self.confirm("Remove this equalizer?", "The EQ filter is removed from PipeWire and audio goes straight to the output. Your presets are kept.", "Remove", go)
                return
            card = self.cards.get(name)
            label = self.label_of(card["sink"]) if card else name
            try:
                pw.write_eq_conf(pw.FLAT_PRESET if slug else pw.read_conf_preset(slug), slug, pw.eq_target_for(name), "%s EQ" % label)
            except (OSError, ValueError) as e:
                self.toast("Could not write config: %s" % e, 5)
                return
            self.restart_pipewire()

        def on_eq_enable(self, name, v):
            self.set_eq_enabled_for(name, v)
            slug = self.slug_of(name)
            ed = self.editors.get(slug)
            if ed:
                ed.bypass = not v
                ed.enable_sw.set_active_quiet(v)
                ed.refresh_graph()
                ed.apply()
                return
            preset = pw.read_live_eq(slug)
            self.worker.run(lambda: pw.apply_live_eq(preset, not v, slug), None, lambda e: self.toast("EQ: %s" % e, 5))
            card = self.cards.get(name)
            if card:
                card["graph"].set_bands(preset["bands"], preset["preamp"], bypass=not v)

        def open_eq_editor(self, name):
            slug = self.slug_of(name)
            card = self.cards.get(name)
            label = self.label_of(card["sink"]) if card else name
            ed = self.editors.get(slug)
            if ed is None:
                def mirror(preset, bypass, n=name):
                    c = self.cards.get(n)
                    if c:
                        c["graph"].set_bands(preset["bands"], preset["preamp"], bypass=bypass)
                        c["eq_sw"].set_active_quiet(not bypass)
                ed = ui.EqEditor(self, slug, pw.eq_target_for(name), "%s EQ" % label,
                                 lambda n=name: self.eq_enabled_for(n), lambda v, n=name: self.set_eq_enabled_for(n, v), mirror)
                self.editors[slug] = ed
            else:
                ed.reload_from_live()
            parent = ed.get_parent()
            if parent is not None:      # still inside a closed dialog: detach so it can be shown again
                parent.set_child(None)
            ui.open_editor_dialog(self, "%s — equalizer" % label, ed)

        def toggle_surround(self):
            if pw.surround_conf_present():
                pw.remove_surround_conf()
            else:
                try:
                    pw.write_surround_conf()
                except (OSError, RuntimeError) as e:
                    self.toast(str(e), 5)
                    return
            self.restart_pipewire()

        def restart_pipewire(self):
            self.banner.set_revealed(False)
            self._stop_mic_monitor()
            self.mic_monitor.set_active_quiet(False)

            def ok(res):
                self.toast("PipeWire restarted" if res else "PipeWire restart failed", 4)
                GLib.timeout_add_seconds(2, self._after_restart)
            self.worker.run(pw.restart, ok, lambda e: self.toast(str(e), 5))

        def _after_restart(self):
            self.refresh_outputs(force=True)
            self.refresh_mic_group()
            for slug, ed in self.editors.items():
                if pw.eq_running(slug):
                    ed.apply()
            return False

        # -------------------------------------------------------------- device handlers
        def on_output(self, idx):
            value = {0: sc.SPK_HEADPHONES, 1: sc.SPK_STEREO, 2: sc.SPK_SURROUND_51}[idx]

            def do(c):
                if value == sc.SPK_HEADPHONES:
                    c.set_speaker_config(sc.SPK_HEADPHONES)
                else:
                    c.set_speaker_config(value)
                    c.set_speaker_config(sc.SPK_TOGGLE_TO_SPEAKER)
                return c.get_speaker_config()

            def ok(spk):
                self.state["speaker"] = spk
                self.apply_status_bits()

            self.bt(do, ok, "output switch")
            self.schedule_commit()

        def on_volume(self, db):
            self.bt(lambda c: c.set_volume_db(db), what="volume")

        def on_mute(self, v):
            self.on_button(sc.BTN_MUTE, v)

        def on_button(self, btn, v):
            self.bt(lambda c: c.set_button(btn, v), what="button")
            self.schedule_commit()

        def on_feature(self, key, v):
            if key == "hp_high_gain" and v:
                self.confirm("Enable high gain?", "High gain is for 600 Ω headphones. It can damage 32 to 300 Ω headphones.",
                             "Enable", lambda: self._set_feature(key, True), lambda: self.feature_rows[key].set_active_quiet(False))
                return
            self._set_feature(key, v)

        def _set_feature(self, key, v):
            def do(c):
                c.set_feature(key, v)
                c.commit()
                return c.get_features()

            def ok(f):
                self.state["features"] = f
                if not (f[0] >> sc.FEATURES[key] & 1) == bool(v):
                    self.toast("The X7 did not accept that switch over Bluetooth", 4)
                    self.feature_rows[key].set_active_quiet(f[0] >> sc.FEATURES[key] & 1)

            self.bt(do, ok, key)

        def restore_defaults(self):
            def go():
                def do(c):
                    c.set_feature("restore_default", True)
                    c.commit()

                self.bt(do, lambda _: GLib.timeout_add_seconds(2, lambda: (self.refresh_all(), False)[1]), "restore")
            self.confirm("Restore factory defaults?", "Every setting inside the X7 goes back to default. The Bluetooth pairing is kept.", "Restore", go)

        def usb_reset(self):
            def err(e):
                hint = " Install the udev rule (see README)." if getattr(e, "errno", None) == 13 else ""
                self.toast("USB reset failed: %s.%s" % (e, hint), 6)
            self.worker.run(usb.reset, lambda out: self.toast(out), err)

        def pair_dialog(self):
            d = Adw.AlertDialog(heading="Pair with the X7",
                                body="Hold the X7's Power/Bluetooth button for about 2 seconds until it blinks blue, then press Pair. "
                                     "This drops the old bond, pairs, and leaves the X7 untrusted so it never grabs your audio on its own.")
            d.add_response("cancel", "Cancel")
            d.add_response("pair", "Pair")
            d.set_response_appearance("pair", Adw.ResponseAppearance.SUGGESTED)

            def resp(dlg, r):
                if r != "pair":
                    return
                self.set_status(False, "Pairing… (scanning up to 40 s)")
                if self.client:
                    self.client.close()
                    self.client = None

                def do():
                    return bluetooth.pair(status=lambda m: GLib.idle_add(self.set_status, False, m))

                def ok(mac):
                    self.cfg["mac"] = mac
                    self.save_config()
                    self.toast("Paired with %s" % mac, 4)
                    self.connect_device()

                def err(e):
                    self.set_status(False, str(e))
                    self.toast(str(e), 6)

                self.worker.run(do, ok, err)
            d.connect("response", resp)
            d.present(self)

        # -------------------------------------------------------------- box EQ presets
        def box_preset_names(self):
            return ["(current)"] + [p["name"] for p in self.cfg.get("box_eq_presets", [])]

        def on_box_preset(self, idx):
            if idx <= 0:
                return
            self.apply_box_preset(self.cfg["box_eq_presets"][idx - 1])

        def apply_box_preset(self, preset):
            values = {sc.P["eq_preamp"]: float(preset["preamp"])}
            for i, gval in enumerate(preset["bands"][:10]):
                values[sc.P["eq_band%d" % i]] = float(gval)
            self.state["pb"].update(values)
            self.boxeq_pre.set_value_quiet(preset["preamp"])
            for i, row in enumerate(self.boxeq_bands):
                row.set_value_quiet(preset["bands"][i])
            self.refresh_box_graph()
            self.bt(lambda c: c.set_params(sc.MODULE_PLAYBACK, values), what="EQ preset")
            self.schedule_commit()

        def save_box_preset(self):
            def save(name):
                preset = {"name": name, "preamp": self.boxeq_pre.get_value(), "bands": [r.get_value() for r in self.boxeq_bands]}
                lst = [p for p in self.cfg.get("box_eq_presets", []) if p["name"] != name] + [preset]
                self.cfg["box_eq_presets"] = lst
                self.save_config()
                self.boxeq_presets.set_items_quiet(self.box_preset_names(), len(lst))
                self.toast("Saved preset '%s'" % name)
            ui.ask_name(self, "Save X7 EQ preset", save)

        def delete_box_preset(self):
            idx = self.boxeq_presets.get_selected() - 1
            if idx < 0:
                self.toast("Select a user preset first")
                return
            name = self.cfg["box_eq_presets"].pop(idx)["name"]
            self.save_config()
            self.boxeq_presets.set_items_quiet(self.box_preset_names(), 0)
            self.toast("Deleted '%s'" % name)

        # -------------------------------------------------------------- voice filter
        def refresh_mic_group(self, dump=None):
            dump = dump if dump is not None else pw.pw_dump()
            self._mic_sources = [s for s in pw.sources(dump) if s["name"] != pw.VOICE_NODE]
            names = [s["description"] or s["name"] for s in self._mic_sources] or ["(no microphone found)"]
            present = pw.voice_conf_present()
            configured = pw.voice_conf_source() or self.cfg.get("voice_source")
            sel = next((i for i, s in enumerate(self._mic_sources) if s["name"] == configured), 0)
            self.mic_source.set_items_quiet(names, sel)
            self.mic_source.set_sensitive(bool(self._mic_sources) and not present)
            node_id, vad, grace = pw.voice_filter_state(dump)
            running = node_id is not None
            self.mic_setup_row.button.set_label("Remove" if present else "Set up")
            if present:
                self.mic_setup_row.set_subtitle("Running" if running else "Configured, restart PipeWire to start it")
            else:
                self.mic_setup_row.set_subtitle("Wraps the microphone above in an RNNoise voice gate" if pw.find_rnnoise()
                                                else "Needs the RNNoise LADSPA plugin (noise-suppression-for-voice)")
            self.mic_setup_row.button.set_sensitive(present or (bool(self._mic_sources) and pw.find_rnnoise() is not None))
            for w in self.mic_widgets:
                w.set_sensitive(running)
            if running:
                self.mic_sw.set_active_quiet(pw.defaults(dump).get("source") == pw.VOICE_NODE)
                self.mic_vad.set_value_quiet(vad)
                self.mic_grace.set_value_quiet(grace)

        def toggle_voice_conf(self):
            if pw.voice_conf_present():
                pw.remove_voice_conf()
                self.restart_pipewire()
                return
            idx = self.mic_source.get_selected()
            if not self._mic_sources or idx >= len(self._mic_sources):
                self.toast("Pick a microphone first")
                return
            src = self._mic_sources[idx]
            try:
                pw.write_voice_conf(src["name"], src["description"] or src["name"], self.mic_vad.get_value() or 85.0, self.mic_grace.get_value() or 250.0)
            except (OSError, RuntimeError, ValueError) as e:
                self.toast(str(e), 6)
                return
            self.cfg["voice_source"] = src["name"]
            self.save_config()
            self.restart_pipewire()

        def on_mic_enable(self, v):
            dump = pw.pw_dump()
            target = pw.find_node(pw.VOICE_NODE, dump) if v else pw.find_node(pw.voice_conf_source() or "", dump)
            if not target:
                self.toast("Microphone node not found")
                return
            self.worker.run(lambda: pw.set_default(target["id"]),
                            lambda ok: self.toast("Default mic: %s" % ("voice-only" if v else "raw microphone") if ok else "Could not set default mic"))

        def on_mic_param(self):
            vad, grace = self.mic_vad.get_value(), self.mic_grace.get_value()
            self.worker.run(lambda: pw.voice_filter_set(vad, grace), None, lambda e: self.toast("Voice filter: %s" % e, 5))

        def _stop_mic_monitor(self):
            proc = self._mic_monitor_proc
            self._mic_monitor_proc = None
            if proc:
                proc.terminate()

        def on_mic_monitor(self, v):
            if not v:
                self._stop_mic_monitor()
                return
            sink = pw.x7_sink()
            sink_name = sink["name"] if sink else pw.defaults().get("sink")
            if not sink_name:
                self.toast("No output sink found")
                self.mic_monitor.set_active_quiet(False)
                return
            try:
                self._mic_monitor_proc = subprocess.Popen(pw.loopback_command(pw.VOICE_NODE, sink_name),
                                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, ValueError) as e:
                self.toast("Monitor failed: %s" % e, 5)
                self.mic_monitor.set_active_quiet(False)

        def mic_test(self):
            d = Adw.AlertDialog(heading="Mic test", body="Recording 6 seconds. Say a sentence, stay quiet for a moment, then tap the desk or type. "
                                                         "The filtered take plays back first, then the raw mic.")
            d.add_response("ok", "Close")
            d.present(self)

            def do():
                pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                env = dict(os.environ, PYTHONPATH=pkg_parent + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else ""))
                # own session so a stuck pw-record/pw-play dies with the test on timeout
                p = subprocess.Popen([sys.executable, "-m", "x7control.mictest", "-s", "6"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, env=env, start_new_session=True)
                try:
                    out, _ = p.communicate(timeout=120)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGTERM)
                    out, _ = p.communicate(timeout=5)
                    out += "\nMic test timed out"
                return out

            def ok(out):
                skip = ("Recording", "Say", "Playing", "Good result", "noise floor drops")
                lines = [line for line in out.splitlines() if line.strip() and not line.strip().startswith(skip)]
                d.set_body("\n".join(lines[-MICTEST_MAX_LINES:]) or out[-800:])
            self.worker.run(do, ok, lambda e: d.set_body(str(e)))

    class X7App(Adw.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
            about = Gio.SimpleAction.new("about", None)
            about.connect("activate", self.on_about)
            self.add_action(about)

        def do_activate(self):
            win = self.props.active_window or X7Window(self)
            win.present()
            if args.smoke:
                GLib.timeout_add(2000, lambda: (self.quit(), False)[1])

        def on_about(self, *_):
            dlg = Adw.AboutDialog(application_name="X7 Control", application_icon=APP_ID, version=__version__,
                                  developer_name="silkhelp-wq and contributors", license_type=Gtk.License.MIT_X11,
                                  website="https://github.com/silkhelp-wq/x7control",
                                  issue_url="https://github.com/silkhelp-wq/x7control/issues",
                                  comments="Sound Blaster X7 settings over Bluetooth, plus per-output PipeWire equalizers, "
                                           "HRTF game surround and an RNNoise voice filter. Not affiliated with Creative Technology.")
            dlg.present(self.props.active_window)

    return X7App()
