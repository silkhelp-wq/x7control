"""X7 Control - Sound Blaster X7 settings over Bluetooth plus the PipeWire headphone EQ.

Pages: Device (output, volume, SBX master, feature switches), SBX Pro Studio effects,
the X7's own 10-band graphic EQ, CrystalVoice mic processing, and the PC-side parametric
EQ / HRTF surround / voice filter that live in PipeWire.
"""
import argparse
import json
import os
import queue
import subprocess
import sys
import threading

from . import APP_ID, __version__, bluetooth, config, pipewire as pw, soundcore as sc, usb

COMMIT_DELAY_MS = 3000       # the Creative app commits 3 s after the last change
SLIDER_DEBOUNCE_MS = 120
POLL_SECONDS = 8
FOCUS_ANGLE_OFFSET = 20      # the app shows wedge angle - 20
PAGES = ("device", "sbx", "boxeq", "mic", "pc")
MICTEST_MAX_LINES = 6


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="x7control", description="Sound Blaster X7 control for Linux")
    ap.add_argument("--version", action="version", version="x7control %s" % __version__)
    ap.add_argument("--page", choices=PAGES, help="open on this page")
    ap.add_argument("--diagnose", action="store_true", help="print environment details for a bug report and exit")
    ap.add_argument("--smoke", action="store_true", help=argparse.SUPPRESS)  # build the window, quit after 2 s (CI)
    return ap.parse_args(argv)


def diagnose():
    """Everything a bug report needs, nothing private: versions, X7 presence, PipeWire nodes."""
    import platform
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
        r = subprocess.run(["sh", "-c", "command -v %s" % tool], capture_output=True, text=True)
        print("%-12s %s" % (tool, r.stdout.strip() or "MISSING"))
    r = subprocess.run(["pw-cli", "--version"], capture_output=True, text=True)
    print("pipewire:    %s" % (r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "unknown"))
    cfg = config.load()
    print("x7 paired:   %s" % ("yes" if cfg.get("mac") else "no"))
    if cfg.get("mac"):
        print("bluetooth:   %s" % (bluetooth.info(cfg["mac"]) or "bluetoothctl gave no info"))
    dev, _ = usb.find_device()
    print("x7 on usb:   %s" % ("yes, serial %s" % usb.serial() if dev else "no"))
    dump = pw.pw_dump()
    x7 = pw.x7_sink(dump)
    print("x7 sink:     %s" % (x7["name"] if x7 else "not found"))
    for label, name in (("eq filter", pw.EQ_NODE), ("surround", pw.SURROUND_NODE), ("voice", pw.VOICE_NODE)):
        print("%-12s %s" % (label + ":", "running" if pw.find_node(name, dump) else "not running"))
    print("eq conf:     %s" % ("present" if pw.eq_conf_present() else "absent"))
    print("rnnoise:     %s" % (pw.find_rnnoise() or "not found"))
    print("sofa:        %s" % (pw.find_sofa() or "not found"))
    print("sources:     " + ", ".join(n["name"] for n in pw.sources(dump)))
    print("sinks:       " + ", ".join(n["name"] for n in pw.sinks(dump)))
    return 0


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.diagnose:
        return diagnose()
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio, GLib  # noqa: F401
    app = build_app(args)
    return app.run([sys.argv[0]])


def build_app(args):
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio, GLib, Gtk

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

    # ------------------------------------------------------------------ widgets
    class SliderRow(Adw.ActionRow):
        """ActionRow with a horizontal scale and a value label. on_change fires debounced."""

        def __init__(self, title, lo, hi, step, fmt="{:.0f}", subtitle=None, on_change=None, digits=0, width=260):
            super().__init__(title=title)
            if subtitle:
                self.set_subtitle(subtitle)
            self.fmt = fmt
            self.on_change = on_change
            self._guard = False
            self._pending = None
            self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, step)
            self.scale.set_digits(digits)
            self.scale.set_draw_value(False)
            self.scale.set_size_request(width, -1)
            self.scale.set_valign(Gtk.Align.CENTER)
            self.scale.set_hexpand(True)
            self.label = Gtk.Label(width_chars=8, xalign=1.0)
            self.label.add_css_class("numeric")
            box = Gtk.Box(spacing=8)
            box.append(self.scale)
            box.append(self.label)
            self.add_suffix(box)
            self.scale.connect("value-changed", self._changed)

        def _changed(self, scale):
            v = scale.get_value()
            self.label.set_text(self.fmt.format(v))
            if self._guard or not self.on_change:
                return
            if self._pending:
                GLib.source_remove(self._pending)
            self._pending = GLib.timeout_add(SLIDER_DEBOUNCE_MS, self._fire)

        def _fire(self):
            self._pending = None
            self.on_change(self.scale.get_value())
            return False

        def set_value_quiet(self, v):
            self._guard = True
            self.scale.set_value(v)
            self.label.set_text(self.fmt.format(v))
            self._guard = False

        def get_value(self):
            return self.scale.get_value()

    def SwitchRow(title, subtitle=None, on_toggle=None):
        """Adw.SwitchRow is a final type, so decorate an instance instead of subclassing."""
        row = Adw.SwitchRow(title=title)
        if subtitle:
            row.set_subtitle(subtitle)
        row._guard = False

        def toggled(*_):
            if not row._guard and on_toggle:
                on_toggle(row.get_active())

        def set_active_quiet(v):
            row._guard = True
            row.set_active(bool(v))
            row._guard = False

        row.set_active_quiet = set_active_quiet
        row.connect("notify::active", toggled)
        return row

    def ComboRow(title, items, subtitle=None, on_select=None):
        row = Adw.ComboRow(title=title)
        if subtitle:
            row.set_subtitle(subtitle)
        row.set_model(Gtk.StringList.new(list(items)))
        row._guard = False

        def selected(*_):
            if not row._guard and on_select:
                on_select(row.get_selected())

        def set_selected_quiet(i):
            row._guard = True
            row.set_selected(max(0, i))
            row._guard = False

        def set_items_quiet(items, sel=0):
            row._guard = True
            row.set_model(Gtk.StringList.new(list(items)))
            row.set_selected(sel)
            row._guard = False

        row.set_selected_quiet = set_selected_quiet
        row.set_items_quiet = set_items_quiet
        row.connect("notify::selected", selected)
        return row

    def button_row(title, subtitle, button_label, on_click, destructive=False):
        row = Adw.ActionRow(title=title, subtitle=subtitle or "")
        btn = Gtk.Button(label=button_label, valign=Gtk.Align.CENTER)
        if destructive:
            btn.add_css_class("destructive-action")
        btn.connect("clicked", lambda *_: on_click())
        row.add_suffix(btn)
        row.set_activatable_widget(btn)
        row.button = btn
        return row

    # ------------------------------------------------------------------ window
    class X7Window(Adw.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title="X7 Control", default_width=880, default_height=760)
            self.set_icon_name(APP_ID)
            self.cfg = config.load()
            self.worker = Worker()
            self.client = None
            self.state = {"pb": {}, "voice": {}, "buttons": {}, "features": (0, 0), "speaker": 1, "vol": None}
            self._commit_source = None
            self._poll_source = None
            self._reconnect_source = None
            self._pc_eq_source = None
            self._banner_action = None
            self._mic_monitor_proc = None
            self.pc_preset = pw.read_live_eq()
            self.pc_eq_bypass = not self.cfg.get("pc_eq_enabled", True)

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
            mbtn = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
            header.pack_end(mbtn)
            refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
            refresh.set_tooltip_text("Re-read everything from the X7")
            refresh.connect("clicked", lambda *_: self.refresh_all())
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
            self.stack.add_titled_with_icon(self.build_pc_page(), "pc", "PC", "preferences-desktop-multimedia-symbolic")

            page = args.page or os.environ.get("X7CONTROL_PAGE")
            if page in PAGES:
                self.stack.set_visible_child_name(page)
            self.connect("close-request", self._on_close)
            self.connect_device()
            self.refresh_pc_page()

        # -------------------------------------------------------------- helpers
        def toast(self, text, timeout=3):
            self.toasts.add_toast(Adw.Toast(title=str(text), timeout=timeout))

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

        # -------------------------------------------------------------- connection
        def connect_device(self):
            mac = self.cfg.get("mac")
            if not mac:
                self.set_status(False, "No X7 paired yet. Use Pair.")
                self.sensitive_device_widgets(False)
                return
            self.set_status(False, "Connecting to %s…" % mac)

            def do():
                c = sc.X7Client(mac, on_event=self._on_event, on_connection=self._on_connection)
                c.connect()
                return c

            def ok(c):
                self.client = c
                self.refresh_all()

            def err(e):
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
            # unsolicited frames (front-panel button, knob): re-read the cheap status bits
            GLib.idle_add(self.refresh_status_bits)

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
                if self._poll_source is None:
                    self._poll_source = GLib.timeout_add_seconds(POLL_SECONDS, self._poll)

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
            if self.stack.get_visible_child_name() == "pc":
                sink = pw.x7_sink()
                if sink:
                    vol, muted = pw.get_volume(sink["id"])
                    self.pc_vol.set_value_quiet(vol)
                    self.pc_mute.set_active_quiet(muted)
            return True

        # -------------------------------------------------------------- state -> UI
        def pb(self, name, default=0.0):
            return self.state["pb"].get(sc.P[name], default)

        def voice(self, name, default=0.0):
            return self.state["voice"].get(sc.V[name], default)

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
            self.svm_mode.set_selected_quiet(int(self.pb("smartvol_mode")))
            self.dialog_sw.set_active_quiet(self.pb("dialogplus_enable"))
            self.dialog_lv.set_value_quiet(self.pb("dialogplus_level") * 100)
            # box EQ
            self.boxeq_sw.set_active_quiet(self.pb("eq_enable"))
            self.boxeq_pre.set_value_quiet(self.pb("eq_preamp"))
            for i, row in enumerate(self.boxeq_bands):
                row.set_value_quiet(self.pb("eq_band%d" % i))
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

        # -------------------------------------------------------------- pages
        def build_device_page(self):
            page = Adw.PreferencesPage()
            self.device_widgets = []

            g = Adw.PreferencesGroup(title="Connection")
            self.conn_row = Adw.ActionRow(title="Sound Blaster X7", subtitle="Not connected")
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
            self.boxeq_sw = SwitchRow("Enable", None, lambda v: self.set_playback("eq_enable", 1.0 if v else 0.0))
            g.add(self.boxeq_sw)
            self.boxeq_presets = ComboRow("Preset", self.box_preset_names(), on_select=self.on_box_preset)
            g.add(self.boxeq_presets)
            self.boxeq_pre = SliderRow("Preamp", -12, 12, 0.5, "{:+.1f} dB", on_change=lambda v: self.set_playback("eq_preamp", v), digits=1)
            g.add(self.boxeq_pre)
            page.add(g)
            g = Adw.PreferencesGroup(title="Bands")
            self.boxeq_bands = []
            for i, hz in enumerate(sc.EQ_BAND_HZ):
                row = SliderRow("%s Hz" % hz, -24, 24, 0.5, "{:+.1f} dB", on_change=lambda v, i=i: self.set_playback("eq_band%d" % i, v), digits=1)
                self.boxeq_bands.append(row)
                g.add(row)
            page.add(g)
            g = Adw.PreferencesGroup(title="Presets")
            g.add(button_row("Save current bands as a preset", None, "Save as…", self.save_box_preset))
            g.add(button_row("Delete the selected user preset", None, "Delete", self.delete_box_preset, destructive=True))
            g.add(button_row("Flatten", "Set every band and the preamp to 0 dB", "Flat", lambda: self.apply_box_preset({"preamp": 0, "bands": [0] * 10})))
            page.add(g)
            return page

        def build_mic_page(self):
            page = Adw.PreferencesPage()
            g = Adw.PreferencesGroup(title="CrystalVoice", description="Processing for the X7's own mic input (the front mic jack or the array), not USB microphones.")
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
            return page

        def build_pc_page(self):
            scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
            clamp = Adw.Clamp(maximum_size=1100, tightening_threshold=900)
            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_top=24, margin_bottom=24, margin_start=12, margin_end=12)
            clamp.set_child(page)
            scroller.set_child(clamp)

            g = Adw.PreferencesGroup(title="PipeWire output", description="What the PC sends to the X7 over USB.")
            self.pc_vol = SliderRow("X7 volume", 0, 100, 1, "{:.0f} %", "The X7 sink volume in PipeWire (this is the hardware volume too)", on_change=self.on_pc_volume)
            self.pc_mute = SwitchRow("Mute X7 sink", None, self.on_pc_mute)
            self.default_row = ComboRow("Default output", ["X7 (headphone EQ)", "Game Surround 7.1 (HRTF)"],
                                        subtitle="New apps play here. Per-app choices in your desktop's audio applet still win.", on_select=self.on_default_sink)
            self.surround_row = button_row("Game surround sink", "", "Set up", self.toggle_surround)
            for r in (self.pc_vol, self.pc_mute, self.default_row, self.surround_row):
                g.add(r)
            page.append(g)

            g = Adw.PreferencesGroup(title="Headphone correction EQ", description="Parametric EQ inserted before the X7 by WirePlumber. Changes apply live.")
            self.pc_eq_setup_row = button_row("Headphone EQ filter", "", "Set up", self.toggle_eq_conf)
            g.add(self.pc_eq_setup_row)
            self.pc_eq_sw = SwitchRow("Enable", "Off = bypass (preamp 0 dB, all gains 0)", self.on_pc_eq_enable)
            g.add(self.pc_eq_sw)
            self.pc_presets = ComboRow("Preset", self.pc_preset_names(), on_select=self.on_pc_preset)
            g.add(self.pc_presets)
            self.pc_pre = SliderRow("Preamp", -20, 10, 0.1, "{:+.1f} dB", "Keep it at minus the largest boost so nothing clips", on_change=self.on_pc_preamp, digits=1)
            g.add(self.pc_pre)
            page.append(g)

            g = Adw.PreferencesGroup(title="Bands")
            self.pc_band_rows = []
            types = list(pw.FILTER_TYPES.keys())
            for i in range(10):
                row = Adw.ActionRow(title="%d" % (i + 1))
                box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
                typ = Gtk.DropDown.new_from_strings(types)
                typ.set_tooltip_text("Filter type (changing it needs a PipeWire restart)")
                freq = Gtk.SpinButton.new_with_range(20, 20000, 1)
                freq.set_tooltip_text("Frequency, Hz")
                freq.set_width_chars(5)
                q = Gtk.SpinButton.new_with_range(0.1, 12, 0.05)
                q.set_digits(2)
                q.set_tooltip_text("Q")
                q.set_width_chars(4)
                gain = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, -15, 15, 0.1)
                gain.set_size_request(200, -1)
                gain.set_draw_value(False)
                glabel = Gtk.Label(width_chars=8, xalign=1.0)
                glabel.add_css_class("numeric")
                for w in (typ, freq, q, gain, glabel):
                    box.append(w)
                row.add_suffix(box)
                g.add(row)
                entry = {"type": typ, "freq": freq, "q": q, "gain": gain, "label": glabel, "guard": False}
                self.pc_band_rows.append(entry)
                typ.connect("notify::selected", lambda *_, i=i: self.on_pc_band(i, type_changed=True))
                freq.connect("value-changed", lambda *_, i=i: self.on_pc_band(i))
                q.connect("value-changed", lambda *_, i=i: self.on_pc_band(i))
                gain.connect("value-changed", lambda *_, i=i: self.on_pc_band(i))
            page.append(g)
            self.pc_bands_group = g

            g = Adw.PreferencesGroup(title="Presets and persistence")
            g.add(button_row("Save current EQ as a preset", None, "Save as…", self.save_pc_preset))
            g.add(button_row("Delete the selected user preset", None, "Delete", self.delete_pc_preset, destructive=True))
            g.add(button_row("Import an AutoEq ParametricEQ.txt", "From github.com/jaakkopasanen/AutoEq results", "Import…", self.import_autoeq))
            g.add(button_row("Make this the startup EQ", "Writes %s" % pw.EQ_CONF, "Write config", self.write_pc_conf))
            g.add(button_row("Restart PipeWire", "Only needed after changing a filter type; audio drops for a second", "Restart", self.restart_pipewire))
            page.append(g)
            self.pc_eq_widgets = [self.pc_eq_sw, self.pc_presets, self.pc_pre, self.pc_bands_group, g]

            g = Adw.PreferencesGroup(title="Voice filter (microphone)", description="RNNoise voice-only microphone: only speech passes, keyboard, fans and room noise are gated out.")
            self.mic_source = ComboRow("Microphone to filter", ["(none found)"], "The raw input the filter wraps")
            self.mic_setup_row = button_row("Voice filter", "", "Set up", self.toggle_voice_conf)
            self.mic_sw = SwitchRow("Use the voice-only mic as the default input", "Off = apps get the raw microphone", self.on_mic_enable)
            self.mic_vad = SliderRow("Voice threshold", 0, 100, 1, "{:.0f} %", "Higher = stricter gate. Lower it if the start of your words gets clipped", on_change=lambda v: self.on_mic_param())
            self.mic_grace = SliderRow("Hold open after speech", 0, 1000, 10, "{:.0f} ms", "How long the gate stays open after you stop talking", on_change=lambda v: self.on_mic_param())
            self.mic_monitor = SwitchRow("Live monitor in headphones", "Hear the filtered mic while you talk, tap the desk, type", self.on_mic_monitor)
            self.mic_test_row = button_row("Record a 6 s test and play it back", "Raw and filtered are recorded together; you hear filtered first, then raw", "Test", self.mic_test)
            for r in (self.mic_source, self.mic_setup_row, self.mic_sw, self.mic_vad, self.mic_grace, self.mic_monitor, self.mic_test_row):
                g.add(r)
            page.append(g)
            self.mic_widgets = [self.mic_sw, self.mic_vad, self.mic_grace, self.mic_monitor, self.mic_test_row]
            return scroller

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

        def confirm(self, heading, body, ok_label, on_ok, on_cancel=None):
            d = Adw.AlertDialog(heading=heading, body=body)
            d.add_response("cancel", "Cancel")
            d.add_response("ok", ok_label)
            d.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
            d.set_default_response("cancel")

            def resp(dlg, r):
                if r == "ok":
                    on_ok()
                elif on_cancel:
                    on_cancel()
            d.connect("response", resp)
            d.present(self)

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
                    config.save(self.cfg)
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
            self.bt(lambda c: c.set_params(sc.MODULE_PLAYBACK, values), what="EQ preset")
            self.schedule_commit()

        def save_box_preset(self):
            def save(name):
                preset = {"name": name, "preamp": self.boxeq_pre.get_value(), "bands": [r.get_value() for r in self.boxeq_bands]}
                lst = [p for p in self.cfg.get("box_eq_presets", []) if p["name"] != name] + [preset]
                self.cfg["box_eq_presets"] = lst
                config.save(self.cfg)
                self.boxeq_presets.set_items_quiet(self.box_preset_names(), len(lst))
                self.toast("Saved preset '%s'" % name)
            self.ask_name("Save X7 EQ preset", save)

        def delete_box_preset(self):
            idx = self.boxeq_presets.get_selected() - 1
            if idx < 0:
                self.toast("Select a user preset first")
                return
            name = self.cfg["box_eq_presets"].pop(idx)["name"]
            config.save(self.cfg)
            self.boxeq_presets.set_items_quiet(self.box_preset_names(), 0)
            self.toast("Deleted '%s'" % name)

        def ask_name(self, heading, on_name):
            d = Adw.AlertDialog(heading=heading)
            entry = Gtk.Entry(placeholder_text="Preset name", activates_default=True, max_length=40)
            d.set_extra_child(entry)
            d.add_response("cancel", "Cancel")
            d.add_response("save", "Save")
            d.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
            d.set_default_response("save")

            def resp(dlg, r):
                name = config.clean_name(entry.get_text())
                if r == "save" and name:
                    on_name(name)
            d.connect("response", resp)
            d.present(self)

        # -------------------------------------------------------------- PC page
        def pc_preset_names(self):
            return ["(current)"] + [p["name"] for p in pw.BUILTIN_PRESETS] + [p["name"] for p in self.cfg.get("pc_eq_presets", [])]

        def refresh_pc_page(self):
            dump = pw.pw_dump()
            sink = pw.x7_sink(dump)
            if sink:
                vol, muted = pw.get_volume(sink["id"])
                self.pc_vol.set_value_quiet(vol)
                self.pc_mute.set_active_quiet(muted)
                self.pc_vol.set_sensitive(True)
                self.pc_vol.set_subtitle("The X7 sink volume in PipeWire (this is the hardware volume too)")
            else:
                self.pc_vol.set_sensitive(False)
                self.pc_vol.set_subtitle("X7 not found on USB")
            d = pw.defaults(dump).get("sink")
            self.default_row.set_selected_quiet(1 if d == pw.SURROUND_NODE else 0)
            # surround sink
            present = pw.surround_conf_present()
            running = pw.find_node(pw.SURROUND_NODE, dump) is not None
            self.surround_row.button.set_label("Remove" if present else "Set up")
            if present:
                self.surround_row.set_subtitle("Virtual 7.1 sink rendered with an HRTF into the headphone EQ" + ("" if running else " (restart PipeWire to start it)"))
            else:
                self.surround_row.set_subtitle("Adds a virtual 7.1 output for games" if pw.find_sofa() else "Needs a SOFA HRTF file (install libmysofa)")
            self.surround_row.button.set_sensitive(present or pw.find_sofa() is not None)
            self.default_row.set_sensitive(running)
            # headphone EQ
            present = pw.eq_conf_present()
            running = pw.find_node(pw.EQ_NODE, dump) is not None
            self.pc_eq_setup_row.button.set_label("Remove" if present else "Set up")
            self.pc_eq_setup_row.set_subtitle(("Running" if running else "Configured, restart PipeWire to start it") if present
                                              else "Adds a 10-band parametric EQ between every app and the X7")
            for w in self.pc_eq_widgets:
                w.set_sensitive(running)
            self.pc_eq_sw.set_active_quiet(not self.pc_eq_bypass)
            self.load_pc_preset_into_ui(self.pc_preset)
            self.refresh_mic_group(dump)

        def load_pc_preset_into_ui(self, preset):
            self.pc_pre.set_value_quiet(preset["preamp"])
            types = list(pw.FILTER_TYPES.keys())
            for i, e in enumerate(self.pc_band_rows):
                b = preset["bands"][i]
                e["guard"] = True
                e["type"].set_selected(types.index(b["type"]) if b["type"] in types else 0)
                e["freq"].set_value(b["freq"])
                e["q"].set_value(b["q"])
                e["gain"].set_value(b["gain"])
                e["label"].set_text("{:+.1f} dB".format(b["gain"]))
                e["guard"] = False

        def collect_pc_preset(self):
            types = list(pw.FILTER_TYPES.keys())
            bands = []
            for e in self.pc_band_rows:
                bands.append({"type": types[e["type"].get_selected()], "freq": e["freq"].get_value(),
                              "q": e["q"].get_value(), "gain": e["gain"].get_value()})
            return {"name": "custom", "preamp": self.pc_pre.get_value(), "bands": bands}

        def on_pc_band(self, i, type_changed=False):
            e = self.pc_band_rows[i]
            e["label"].set_text("{:+.1f} dB".format(e["gain"].get_value()))
            if e["guard"]:
                return
            if type_changed:
                self.show_banner("A filter type changed. Write the config and restart PipeWire to apply it.", "Write + restart",
                                 lambda: (self.write_pc_conf(), self.restart_pipewire()))
            self.schedule_pc_apply()

        def on_pc_preamp(self, _v):
            self.schedule_pc_apply()

        def schedule_pc_apply(self):
            if self._pc_eq_source:
                GLib.source_remove(self._pc_eq_source)
            self._pc_eq_source = GLib.timeout_add(SLIDER_DEBOUNCE_MS, self.apply_pc_eq)

        def apply_pc_eq(self):
            self._pc_eq_source = None
            self.pc_preset = self.collect_pc_preset()
            preset, bypass = self.pc_preset, self.pc_eq_bypass
            self.worker.run(lambda: pw.apply_live_eq(preset, bypass), None, lambda e: self.toast("EQ: %s" % e, 5))
            return False

        def on_pc_eq_enable(self, v):
            self.pc_eq_bypass = not v
            self.cfg["pc_eq_enabled"] = bool(v)
            config.save(self.cfg)
            self.apply_pc_eq()

        def on_pc_preset(self, idx):
            if idx == 0:
                return
            builtin = pw.BUILTIN_PRESETS
            preset = builtin[idx - 1] if idx - 1 < len(builtin) else self.cfg["pc_eq_presets"][idx - 1 - len(builtin)]
            self.pc_preset = json.loads(json.dumps(preset))
            self.load_pc_preset_into_ui(self.pc_preset)
            self.apply_pc_eq()

        def save_pc_preset(self):
            def save(name):
                preset = self.collect_pc_preset()
                preset["name"] = name
                lst = [p for p in self.cfg.get("pc_eq_presets", []) if p["name"] != name] + [preset]
                self.cfg["pc_eq_presets"] = lst
                config.save(self.cfg)
                self.pc_presets.set_items_quiet(self.pc_preset_names(), len(self.pc_preset_names()) - 1)
                self.toast("Saved preset '%s'" % name)
            self.ask_name("Save PC EQ preset", save)

        def delete_pc_preset(self):
            idx = self.pc_presets.get_selected() - 1 - len(pw.BUILTIN_PRESETS)
            if idx < 0:
                self.toast("Select a user preset first")
                return
            name = self.cfg["pc_eq_presets"].pop(idx)["name"]
            config.save(self.cfg)
            self.pc_presets.set_items_quiet(self.pc_preset_names(), 0)
            self.toast("Deleted '%s'" % name)

        def import_autoeq(self):
            dialog = Gtk.FileDialog(title="Import AutoEq ParametricEQ.txt")
            f = Gtk.FileFilter()
            f.set_name("Text files")
            f.add_pattern("*.txt")
            filters = Gio.ListStore.new(Gtk.FileFilter)
            filters.append(f)
            dialog.set_filters(filters)

            def done(dlg, res):
                try:
                    gfile = dlg.open_finish(res)
                except GLib.Error:
                    return
                path = gfile.get_path()
                if not path:
                    self.toast("Only local files can be imported")
                    return
                try:
                    with open(path, encoding="utf-8", errors="replace") as fh:
                        text = fh.read(256 * 1024)
                except OSError as e:
                    self.toast("Could not read the file: %s" % e, 5)
                    return
                preset = config.clean_pc_preset(pw.parse_autoeq(text))
                preset["name"] = config.clean_name(os.path.basename(os.path.dirname(path)) or "Imported")
                self.pc_preset = preset
                self.load_pc_preset_into_ui(preset)
                self.apply_pc_eq()
                self.show_banner("Imported %d filters. Filter types come from the file: write the config and restart PipeWire once." % len(preset["bands"]),
                                 "Write + restart", lambda: (self.write_pc_conf(), self.restart_pipewire()))
            dialog.open(self, None, done)

        def write_pc_conf(self):
            preset = self.collect_pc_preset()
            try:
                pw.write_eq_conf(preset)
                self.toast("Wrote %s" % pw.EQ_CONF, 4)
            except OSError as e:
                self.toast("Could not write config: %s" % e, 5)

        def toggle_eq_conf(self):
            if pw.eq_conf_present():
                def go():
                    pw.remove_eq_conf()
                    self.restart_pipewire()
                self.confirm("Remove the headphone EQ?", "The EQ filter is removed from PipeWire and audio goes straight to the X7. Your presets are kept.", "Remove", go)
                return
            try:
                pw.write_eq_conf(self.collect_pc_preset())
            except OSError as e:
                self.toast("Could not write config: %s" % e, 5)
                return
            self.restart_pipewire()

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
                GLib.timeout_add_seconds(2, lambda: (self.refresh_pc_page(), self.apply_pc_eq_if_running(), False)[2])
            self.worker.run(pw.restart, ok, lambda e: self.toast(str(e), 5))

        def apply_pc_eq_if_running(self):
            if pw.find_node(pw.EQ_NODE):
                self.apply_pc_eq()

        def on_pc_volume(self, v):
            sink = pw.x7_sink()
            if sink:
                self.worker.run(lambda: pw.set_volume(sink["id"], v))

        def on_pc_mute(self, v):
            sink = pw.x7_sink()
            if sink:
                self.worker.run(lambda: pw.set_mute(sink["id"], v))

        def on_default_sink(self, idx):
            dump = pw.pw_dump()
            target = pw.find_node(pw.SURROUND_NODE, dump) if idx == 1 else pw.x7_sink(dump)
            if not target:
                self.toast("Output sink not found")
                return
            self.worker.run(lambda: pw.set_default(target["id"]), lambda ok: self.toast("Default output set" if ok else "Could not set default output"))

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
            config.save(self.cfg)
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
                p = subprocess.run([sys.executable, "-m", "x7control.mictest", "-s", "6"], capture_output=True, text=True, timeout=120, env=env)
                return p.stdout + p.stderr

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
                                  comments="Sound Blaster X7 settings over Bluetooth, plus a PipeWire headphone EQ, "
                                           "HRTF game surround and an RNNoise voice filter. Not affiliated with Creative Technology.")
            dlg.present(self.props.active_window)

    return X7App()
