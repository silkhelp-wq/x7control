"""GTK widgets shared by the app pages: rows, the EQ response graph and the EQ editor.

Imported lazily by app.build_app() so `x7control --diagnose` and the unit tests never need a
display or the GTK bindings.
"""
import json
import math
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_foreign("cairo")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import config, eqcurve, pipewire as pw  # noqa: E402

SLIDER_DEBOUNCE_MS = 120


# ---- rows -----------------------------------------------------------------------
class SliderRow(Adw.ActionRow):
    """ActionRow with a horizontal scale and a value label. on_change fires debounced."""

    def __init__(self, title, lo, hi, step, fmt="{:.0f}", subtitle=None, on_change=None, digits=0, width=260):
        super().__init__(title=title, use_markup=False)
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
    row = Adw.SwitchRow(title=title, use_markup=False)
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
    row = Adw.ComboRow(title=title, use_markup=False)
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
    row = Adw.ActionRow(title=title, subtitle=subtitle or "", use_markup=False)
    btn = Gtk.Button(label=button_label, valign=Gtk.Align.CENTER)
    if destructive:
        btn.add_css_class("destructive-action")
    btn.connect("clicked", lambda *_: on_click())
    row.add_suffix(btn)
    row.set_activatable_widget(btn)
    row.button = btn
    return row


def help_group(title, description, items):
    """A group of expandable explanations: [(heading, text)...]."""
    g = Adw.PreferencesGroup(title=title, description=description)
    for heading, text in items:
        ex = Adw.ExpanderRow(title=heading, use_markup=False)
        body = Adw.ActionRow(subtitle=text, use_markup=False)
        body.add_css_class("property")
        ex.add_row(body)
        g.add(ex)
    return g


def confirm(parent, heading, body, ok_label, on_ok, on_cancel=None):
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
    d.present(parent)


def ask_name(parent, heading, on_name, initial=""):
    d = Adw.AlertDialog(heading=heading)
    entry = Gtk.Entry(placeholder_text="Name", activates_default=True, max_length=60, text=initial)
    d.set_extra_child(entry)
    d.add_response("cancel", "Cancel")
    d.add_response("save", "Save")
    d.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
    d.set_default_response("save")

    def resp(dlg, r):
        name = config.clean_name(entry.get_text(), 60)
        if r == "save" and name:
            on_name(name)
    d.connect("response", resp)
    d.present(parent)


# ---- equalizer help text ---------------------------------------------------------------
ZONE_HELP = [("%s (%s to %s)" % (name, eqcurve.fmt_hz(lo), eqcurve.fmt_hz(hi)), what[0].upper() + what[1:] + ".")
             for lo, hi, name, what in eqcurve.ZONES]
EQ_BASICS = [
    ("What the graph shows",
     "Left to right is pitch, from deep bass to high treble; the labels along the top name each region. "
     "Up and down is how much louder (+) or quieter (−) that pitch becomes. A flat line at 0 dB means the sound is unchanged. "
     "Move a slider and watch the line bend; hover over the graph to read the exact value at any pitch."),
    ("Small moves go a long way",
     "2 to 3 dB is clearly audible; 6 dB is a big change. Make a change, then flip the Enable switch off and on to compare. "
     "If you cannot hear the difference, the change is probably not needed."),
    ("Cut before you boost",
     "Boosting pushes the signal toward clipping (distortion). Cutting what you do not want and turning the volume up a little gives "
     "the same balance more cleanly. If you do boost, lower the preamp by the same amount as your biggest boost so nothing clips."),
    ("Fix problems, then taste",
     "Start flat. If something bothers you (boomy, muddy, harsh, hissy), find the region in the list below and cut it a little. "
     "Only then add taste, such as a gentle bass or treble lift."),
]
PARAMETRIC_HELP = [("Type, frequency, Q and gain",
                    "Type is the shape of the change. Frequency is where it happens on the bass-to-treble scale. Q is how wide: "
                    "a low Q spreads over several octaves, a high Q touches one narrow slice. Gain is how much louder or quieter, in dB.")]
PARAMETRIC_HELP += [(k.capitalize(), v) for k, v in eqcurve.FILTER_HELP.items()]
PARAMETRIC_HELP += [("Headphone presets (AutoEq)",
                     "AutoEq measures headphones and computes bands that flatten them toward a neutral target. Import the ParametricEQ.txt "
                     "for your model from the AutoEq project, then adjust to taste. The preamp in the file keeps the boosts from clipping."),
                    ("Speaker and room correction (REW)",
                     "For speakers, measure with Room EQ Wizard and a measurement microphone, let it generate filters, export them as "
                     "'Filter Settings as text' and import that file here. Correct only the bass region below about 300 Hz where the room "
                     "dominates; leave the rest gentle. Without a measurement, cut what bothers you rather than boosting.")]


# ---- response graph ------------------------------------------------------------------
class EqGraph(Gtk.DrawingArea):
    """Live frequency-response plot of a set of {type, freq, q, gain} bands plus preamp."""

    LEFT, RIGHT, TOP, BOTTOM = 46, 14, 24, 24
    F_TICKS = [(20, "20"), (50, "50"), (100, "100"), (200, "200"), (500, "500"), (1000, "1k"), (2000, "2k"), (5000, "5k"), (10000, "10k"), (20000, "20k")]

    def __init__(self, height=230, compact=False):
        super().__init__(content_height=height, hexpand=True)
        self.compact = compact
        if compact:
            self.LEFT, self.RIGHT, self.TOP, self.BOTTOM = 34, 8, 16, 16
        self.bands, self.preamp, self.bypass = [], 0.0, False
        self.points = []
        self.hover_x = None
        self.set_draw_func(self._draw)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._motion)
        motion.connect("leave", self._leave)
        self.add_controller(motion)
        self.readout = Gtk.Label(xalign=0, label="Hover over the graph to read the response at any pitch.")
        self.readout.add_css_class("dim-label")
        self.readout.add_css_class("caption")
        self.readout.set_wrap(True)

    def set_bands(self, bands, preamp=0.0, bypass=False):
        self.bands, self.preamp, self.bypass = list(bands), float(preamp), bool(bypass)
        self.points = eqcurve.response([] if bypass else self.bands, 0.0 if bypass else self.preamp)
        self.queue_draw()

    # geometry
    def _fx(self, f, w):
        span = math.log10(eqcurve.F_MAX) - math.log10(eqcurve.F_MIN)
        return self.LEFT + (math.log10(max(f, eqcurve.F_MIN)) - math.log10(eqcurve.F_MIN)) / span * (w - self.LEFT - self.RIGHT)

    def _xf(self, x, w):
        span = math.log10(eqcurve.F_MAX) - math.log10(eqcurve.F_MIN)
        t = (x - self.LEFT) / max(1.0, w - self.LEFT - self.RIGHT)
        return 10 ** (math.log10(eqcurve.F_MIN) + max(0.0, min(1.0, t)) * span)

    def _ymax(self):
        peak = max([abs(db) for _, db in self.points] + [0.0])
        return float(min(30, max(6, 3 * math.ceil((peak + 2) / 3))))

    def _fy(self, db, h, ymax):
        return self.TOP + (ymax - max(-ymax, min(ymax, db))) / (2 * ymax) * (h - self.TOP - self.BOTTOM)

    def _db_at(self, f):
        best = min(self.points, key=lambda p: abs(math.log(p[0]) - math.log(f))) if self.points else (f, 0.0)
        return best[1]

    def _motion(self, _c, x, y):
        self.hover_x = x
        f = self._xf(x, self.get_width())
        zone, what = eqcurve.zone_for(f)
        self.readout.set_text("%s: %+.1f dB · %s, %s" % (eqcurve.fmt_hz(f), self._db_at(f), zone.lower(), what))
        self.queue_draw()

    def _leave(self, _c):
        self.hover_x = None
        self.readout.set_text("Hover over the graph to read the response at any pitch.")
        self.queue_draw()

    def _colors(self):
        fg = self.get_color()
        accent = Gdk.RGBA()
        accent.parse("#3584e4")
        sm = Adw.StyleManager.get_default()
        if hasattr(sm, "get_accent_color_rgba"):
            try:
                accent = sm.get_accent_color_rgba()
            except Exception:  # noqa: BLE001, S110 - cosmetic fallback to the default blue
                pass
        return fg, accent

    def _draw(self, _area, cr, w, h):
        fg, accent = self._colors()
        ymax = self._ymax()
        plot_h = h - self.TOP - self.BOTTOM
        cr.select_font_face("sans-serif")
        cr.set_font_size(9 if self.compact else 10)
        # listening regions as alternating stripes with their names along the top
        for i, (lo, hi, name, _what) in enumerate(eqcurve.ZONES):
            x0, x1 = self._fx(lo, w), self._fx(hi, w)
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.05 if i % 2 else 0.02)
            cr.rectangle(x0, self.TOP, x1 - x0, plot_h)
            cr.fill()
            if not self.compact:
                cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.65)
                label = name if x1 - x0 > cr.text_extents(name).width + 6 else eqcurve.ZONE_SHORT.get(name, name[:4])
                if x1 - x0 < cr.text_extents(label).width + 4:
                    label = ""
                cr.move_to(x0 + 3, self.TOP - 8)
                cr.show_text(label)
        # grid: frequency ticks
        cr.set_line_width(1)
        for f, label in self.F_TICKS:
            x = round(self._fx(f, w)) + 0.5
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.12)
            cr.move_to(x, self.TOP)
            cr.line_to(x, self.TOP + plot_h)
            cr.stroke()
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.7)
            ext = cr.text_extents(label)
            cr.move_to(x - ext.width / 2, h - 5)
            cr.show_text(label)
        # grid: dB ticks
        step = 3 if ymax <= 12 else 6 if ymax <= 24 else 10
        if self.compact and step == 3:
            step = 6
        db = -ymax
        while db <= ymax + 0.01:
            y = round(self._fy(db, h, ymax)) + 0.5
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.35 if abs(db) < 0.01 else 0.12)
            cr.move_to(self.LEFT, y)
            cr.line_to(w - self.RIGHT, y)
            cr.stroke()
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.7)
            label = "%+d" % db if db else ("0" if self.compact else "0 dB")
            ext = cr.text_extents(label)
            cr.move_to(self.LEFT - ext.width - 5, y + 3.5)
            cr.show_text(label)
            db += step
        # the response curve, filled to the 0 dB line
        if self.points:
            zero_y = self._fy(0, h, ymax)
            cr.new_path()
            first = True
            for f, db in self.points:
                x, y = self._fx(f, w), self._fy(db, h, ymax)
                cr.line_to(x, y) if not first else cr.move_to(x, y)
                first = False
            path = cr.copy_path()
            cr.line_to(self._fx(self.points[-1][0], w), zero_y)
            cr.line_to(self._fx(self.points[0][0], w), zero_y)
            cr.close_path()
            cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.10 if self.bypass else 0.18)
            cr.fill()
            cr.new_path()
            cr.append_path(path)
            cr.set_line_width(2.2)
            cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.5 if self.bypass else 1.0)
            cr.stroke()
            if not self.bypass:
                for b in self.bands:
                    if b.get("type", "peaking") in ("lowpass", "highpass", "notch") or abs(float(b.get("gain", 0))) < 0.05:
                        continue
                    x, y = self._fx(float(b["freq"]), w), self._fy(self._db_at(float(b["freq"])), h, ymax)
                    cr.arc(x, y, 3 if self.compact else 4, 0, 2 * math.pi)
                    cr.set_source_rgba(accent.red, accent.green, accent.blue, 1.0)
                    cr.fill_preserve()
                    cr.set_source_rgba(1, 1, 1, 0.9)
                    cr.set_line_width(1.2)
                    cr.stroke()
        if self.bypass:
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.6)
            cr.set_font_size(11 if self.compact else 12)
            msg = "Bypassed: sound is unchanged"
            ext = cr.text_extents(msg)
            cr.move_to((w - ext.width) / 2, self.TOP + plot_h / 2 - 8)
            cr.show_text(msg)
        if self.hover_x is not None and self.LEFT <= self.hover_x <= w - self.RIGHT:
            x = round(self.hover_x) + 0.5
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.4)
            cr.set_line_width(1)
            cr.move_to(x, self.TOP)
            cr.line_to(x, self.TOP + plot_h)
            cr.stroke()


def graph_card(graph):
    """A graph and its readout label in a card."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    box.add_css_class("card")
    graph.set_margin_top(6)
    graph.set_margin_start(6)
    graph.set_margin_end(6)
    graph.readout.set_margin_start(12)
    graph.readout.set_margin_end(12)
    graph.readout.set_margin_bottom(8)
    box.append(graph)
    box.append(graph.readout)
    return box


# ---- parametric EQ editor -------------------------------------------------------------
class EqEditor(Gtk.Box):
    """Everything needed to edit one output's parametric EQ: enable, presets, preamp, graph,
    ten band rows, save/import/write. `host` provides cfg, save_config(), toast(), worker,
    window, restart_pipewire(); `on_change(preset, bypass)` lets the caller mirror the curve.
    """

    def __init__(self, host, slug, target, description, get_enabled, set_enabled, on_change=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        self.host, self.slug, self.target, self.description = host, slug, target, description
        self.get_enabled, self.set_enabled, self.on_change = get_enabled, set_enabled, on_change
        self._apply_source = None
        self.preset = pw.read_live_eq(slug)
        self.bypass = not get_enabled()

        self.banner = Adw.Banner(revealed=False)
        self.banner.connect("button-clicked", lambda *_: (self.write_conf(), self.host.restart_pipewire()))
        self.append(self.banner)

        g = Adw.PreferencesGroup(title="Equalizer", description=description)
        self.enable_sw = SwitchRow("Enable", "Off = bypass (preamp 0 dB, all gains 0). Flip it to compare.", self.on_enable)
        self.presets = ComboRow("Preset", self.preset_names(), on_select=self.on_preset)
        self.pre = SliderRow("Preamp", -20, 10, 0.1, "{:+.1f} dB", "Keep it at minus the largest boost so nothing clips", on_change=lambda v: self.on_edit(), digits=1)
        for r in (self.enable_sw, self.presets, self.pre):
            g.add(r)
        self.append(g)

        g = Adw.PreferencesGroup(title="Response", description="The combined effect of all ten bands and the preamp. Dots mark where each band sits.")
        self.graph = EqGraph()
        g.add(graph_card(self.graph))
        self.append(g)

        g = Adw.PreferencesGroup(title="Bands", description="Type · frequency (Hz) · Q (width: low is wide and gentle, high is narrow and surgical) · gain. "
                                                            "Each row explains itself as you change it.")
        self.rows = []
        types = list(pw.FILTER_TYPES.keys())
        for i in range(10):
            row = Adw.ActionRow(title="%d" % (i + 1), use_markup=False)
            box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
            typ = Gtk.DropDown.new_from_strings(types)
            typ.set_tooltip_text("Filter shape. Bell = bump; shelf = everything on one side; pass = remove one side; notch = narrow cut. "
                                 "Changing it needs a PipeWire restart.")
            freq = Gtk.SpinButton.new_with_range(20, 20000, 1)
            freq.set_tooltip_text("Centre or corner frequency in Hz: where on the bass-to-treble scale the band works")
            freq.set_width_chars(5)
            q = Gtk.SpinButton.new_with_range(0.1, 12, 0.05)
            q.set_digits(2)
            q.set_tooltip_text("Q, the width. 0.5 is broad and gentle, 1 to 2 is typical, 5+ is a narrow surgical cut")
            q.set_width_chars(4)
            gain = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, -15, 15, 0.1)
            gain.set_size_request(200, -1)
            gain.set_draw_value(False)
            gain.set_tooltip_text("Gain in dB: right makes this region louder, left makes it quieter")
            glabel = Gtk.Label(width_chars=8, xalign=1.0)
            glabel.add_css_class("numeric")
            for w in (typ, freq, q, gain, glabel):
                box.append(w)
            row.add_suffix(box)
            g.add(row)
            entry = {"type": typ, "freq": freq, "q": q, "gain": gain, "label": glabel, "guard": False, "row": row}
            self.rows.append(entry)
            typ.connect("notify::selected", lambda *_, i=i: self.on_band(i, type_changed=True))
            freq.connect("value-changed", lambda *_, i=i: self.on_band(i))
            q.connect("value-changed", lambda *_, i=i: self.on_band(i))
            gain.connect("value-changed", lambda *_, i=i: self.on_band(i))
        self.append(g)

        g = Adw.PreferencesGroup(title="Presets and persistence")
        g.add(button_row("Save current EQ as a preset", None, "Save as…", self.save_preset))
        g.add(button_row("Delete the selected user preset", None, "Delete", self.delete_preset, destructive=True))
        g.add(button_row("Import AutoEq or REW filters", "A ParametricEQ.txt from AutoEq, or REW's 'Filter Settings as text' export", "Import…", self.import_file))
        g.add(button_row("Make this the startup EQ", "Writes %s" % os.path.basename(pw.eq_conf_path(slug)), "Write config", self.write_conf))
        g.add(button_row("Restart PipeWire", "Only needed after changing a filter type; audio drops for a second", "Restart", self.host.restart_pipewire))
        self.append(g)
        self.append(help_group("New to equalizers?", "Short answers to the usual questions.", EQ_BASICS + PARAMETRIC_HELP + ZONE_HELP))

        self.enable_sw.set_active_quiet(not self.bypass)
        self.load(self.preset)

    # -- state
    def preset_names(self):
        return ["(current)"] + [p["name"] for p in pw.BUILTIN_PRESETS] + [p["name"] for p in self.host.cfg.get("pc_eq_presets", [])]

    def load(self, preset):
        self.pre.set_value_quiet(preset["preamp"])
        types = list(pw.FILTER_TYPES.keys())
        for i, e in enumerate(self.rows):
            b = preset["bands"][i]
            e["guard"] = True
            e["type"].set_selected(types.index(b["type"]) if b["type"] in types else 0)
            e["freq"].set_value(b["freq"])
            e["q"].set_value(b["q"])
            e["gain"].set_value(b["gain"])
            e["label"].set_text("{:+.1f} dB".format(b["gain"]))
            e["row"].set_subtitle(eqcurve.describe_band(b))
            e["guard"] = False
        self.refresh_graph(preset)

    def collect(self):
        types = list(pw.FILTER_TYPES.keys())
        bands = [{"type": types[e["type"].get_selected()], "freq": e["freq"].get_value(), "q": e["q"].get_value(), "gain": e["gain"].get_value()}
                 for e in self.rows]
        return {"name": "custom", "preamp": self.pre.get_value(), "bands": bands}

    def refresh_graph(self, preset=None):
        preset = preset or self.collect()
        self.graph.set_bands(preset["bands"], preset["preamp"], bypass=self.bypass)
        if self.on_change:
            self.on_change(preset, self.bypass)

    def reload_from_live(self):
        self.preset = pw.read_live_eq(self.slug)
        self.bypass = not self.get_enabled()
        self.enable_sw.set_active_quiet(not self.bypass)
        self.load(self.preset)

    # -- handlers
    def on_band(self, i, type_changed=False):
        e = self.rows[i]
        e["label"].set_text("{:+.1f} dB".format(e["gain"].get_value()))
        if e["guard"]:
            return
        preset = self.collect()
        e["row"].set_subtitle(eqcurve.describe_band(preset["bands"][i]))
        self.refresh_graph(preset)
        if type_changed:
            self.banner.set_title("A filter type changed. Write the config and restart PipeWire to apply it.")
            self.banner.set_button_label("Write + restart")
            self.banner.set_revealed(True)
        self.schedule_apply()

    def on_edit(self):
        self.refresh_graph()
        self.schedule_apply()

    def schedule_apply(self):
        if self._apply_source:
            GLib.source_remove(self._apply_source)
        self._apply_source = GLib.timeout_add(SLIDER_DEBOUNCE_MS, self.apply)

    def apply(self):
        self._apply_source = None
        self.preset = self.collect()
        preset, bypass, slug = self.preset, self.bypass, self.slug
        self.host.worker.run(lambda: pw.apply_live_eq(preset, bypass, slug), None, lambda e: self.host.toast("EQ: %s" % e, 5))
        return False

    def on_enable(self, v):
        self.bypass = not v
        self.set_enabled(bool(v))
        self.refresh_graph()
        self.apply()

    def on_preset(self, idx):
        if idx == 0:
            return
        builtin = pw.BUILTIN_PRESETS
        preset = builtin[idx - 1] if idx - 1 < len(builtin) else self.host.cfg["pc_eq_presets"][idx - 1 - len(builtin)]
        self.preset = json.loads(json.dumps(preset))
        self.load(self.preset)
        self.apply()

    def save_preset(self):
        def save(name):
            preset = self.collect()
            preset["name"] = name
            lst = [p for p in self.host.cfg.get("pc_eq_presets", []) if p["name"] != name] + [preset]
            self.host.cfg["pc_eq_presets"] = lst
            self.host.save_config()
            self.presets.set_items_quiet(self.preset_names(), len(self.preset_names()) - 1)
            self.host.toast("Saved preset '%s'" % name)
        ask_name(self.host.window, "Save EQ preset", save)

    def delete_preset(self):
        idx = self.presets.get_selected() - 1 - len(pw.BUILTIN_PRESETS)
        if idx < 0:
            self.host.toast("Select a user preset first")
            return
        name = self.host.cfg["pc_eq_presets"].pop(idx)["name"]
        self.host.save_config()
        self.presets.set_items_quiet(self.preset_names(), 0)
        self.host.toast("Deleted '%s'" % name)

    def import_file(self):
        dialog = Gtk.FileDialog(title="Import AutoEq / REW filters (text)")
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
                self.host.toast("Only local files can be imported")
                return
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    text = fh.read(256 * 1024)
            except OSError as e:
                self.host.toast("Could not read the file: %s" % e, 5)
                return
            preset = config.clean_pc_preset(pw.parse_autoeq(text))
            preset["name"] = config.clean_name(os.path.basename(os.path.dirname(path)) or "Imported")
            self.preset = preset
            self.load(preset)
            self.apply()
            self.banner.set_title("Imported %d filters. Filter types come from the file: write the config and restart PipeWire once." % len(preset["bands"]))
            self.banner.set_button_label("Write + restart")
            self.banner.set_revealed(True)
        dialog.open(self.host.window, None, done)

    def write_conf(self):
        try:
            pw.write_eq_conf(self.collect(), self.slug, self.target, self.description)
            self.host.toast("Wrote %s" % os.path.basename(pw.eq_conf_path(self.slug)), 4)
        except (OSError, ValueError) as e:
            self.host.toast("Could not write config: %s" % e, 5)


def open_editor_dialog(parent, title, editor):
    """Show an EqEditor in a large dialog."""
    dlg = Adw.Dialog(title=title, content_width=940, content_height=860)
    view = Adw.ToolbarView()
    view.add_top_bar(Adw.HeaderBar())
    scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
    clamp = Adw.Clamp(maximum_size=1100, tightening_threshold=900)
    editor.set_margin_top(12)
    editor.set_margin_bottom(24)
    editor.set_margin_start(12)
    editor.set_margin_end(12)
    clamp.set_child(editor)
    scroller.set_child(clamp)
    view.set_content(scroller)
    dlg.set_child(view)
    dlg.present(parent)
    return dlg
