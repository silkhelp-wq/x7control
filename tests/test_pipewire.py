import re

import pytest

from x7control import config, pipewire as pw

AUTOEQ = """Preamp: -4.8 dB
Filter 1: ON LSC Fc 105 Hz Gain -2.1 dB Q 0.70
Filter 2: ON PK Fc 10000 Hz Gain 6.1 dB Q 0.40
Filter 3: OFF PK Fc 1121 Hz Gain -2.7 dB Q 0.57
Filter 4: ON HSC Fc 10000 Hz Gain -3.6 dB Q 0.70
"""


def test_parse_autoeq_skips_off_filters_and_pads():
    p = pw.parse_autoeq(AUTOEQ)
    assert p["preamp"] == -4.8
    assert p["bands"][0] == {"type": "lowshelf", "freq": 105.0, "gain": -2.1, "q": 0.7}
    assert p["bands"][2]["type"] == "highshelf"
    assert len(p["bands"]) == 10
    assert p["bands"][9]["gain"] == 0.0


def test_eq_conf_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(pw, "EQ_CONF", str(tmp_path / "eq.conf"))
    pw.write_eq_conf(pw.HD598CS_PRESET)
    back = pw.read_conf_preset()
    assert back["preamp"] == pytest.approx(-4.8, abs=0.05)
    for a, b in zip(back["bands"], pw.HD598CS_PRESET["bands"], strict=True):
        assert a["type"] == b["type"]
        assert a["freq"] == pytest.approx(b["freq"], abs=0.1)
        assert a["q"] == pytest.approx(b["q"], abs=0.01)
        assert a["gain"] == pytest.approx(b["gain"], abs=0.1)


def test_eq_conf_name_cannot_break_out_of_comment():
    text = pw.eq_conf_text(dict(pw.FLAT_PRESET, name='x"\n}\ncontext.exec = [ { path = "sh" } ]'))
    assert re.search(r"^# Written by X7 Control \(preset: [^\n]*\)\. Preamp", text, re.M)
    assert not re.search(r"^\s*context\.exec", text, re.M)
    assert text.count("context.modules") == 1


def test_filter_type_tables_agree():
    assert tuple(pw.FILTER_TYPES) == config.FILTER_TYPES


def test_voice_conf_rejects_bad_source():
    with pytest.raises(ValueError):
        pw.voice_conf_text('alsa_input.x" } context.exec', "Mic", "/usr/lib/ladspa/librnnoise_ladspa.so")
    with pytest.raises(ValueError):
        pw.voice_conf_text("has space", "Mic", "/usr/lib/ladspa/librnnoise_ladspa.so")


def test_voice_conf_quotes_description():
    text = pw.voice_conf_text("alsa_input.usb-Foo-00.mono-fallback", 'Blue "Snowball"\nnode.name = evil', "/usr/lib/ladspa/librnnoise_ladspa.so")
    assert 'node.description = "Blue \\"Snowball\\" node.name = evil"' in text
    assert 'target.object      = "alsa_input.usb-Foo-00.mono-fallback"' in text
    assert "\nnode.name = evil" not in text


def test_quote_escapes_and_drops_control_characters():
    q = pw._quote('Blue\t"Snow\\ball"\n\x00')
    assert q == '"Blue \\"Snow\\\\ball\\"  "'
    assert all(ord(c) >= 32 for c in q)
    assert len(pw._quote("x" * 1000)) == 202


def test_node_name_rejects_trailing_newline():
    assert config.valid_node_name("alsa_input.foo")
    assert not config.valid_node_name("alsa_input.foo\n")
    assert not config.valid_node_name("")


def test_read_conf_preset_survives_garbage_numbers(tmp_path, monkeypatch):
    p = tmp_path / "eq.conf"
    monkeypatch.setattr(pw, "EQ_CONF", str(p))
    text = pw.eq_conf_text(pw.FLAT_PRESET).replace('"Mult" = 1.0000', '"Mult" = .').replace("eq1 ", "eq1" + "9" * 5000 + " ")
    p.write_text(text)
    assert pw.read_conf_preset()["bands"]  # falls back instead of raising


def test_find_node_ignores_empty_name_and_non_nodes():
    dump = [{"id": 0, "type": "PipeWire:Interface:Core", "info": {"props": {}}},
            {"id": 7, "type": "PipeWire:Interface:Node", "info": {"props": {"node.name": "x7control-voice"}}}]
    assert pw.find_node("", dump) is None
    assert pw.find_node(None, dump) is None
    assert pw.find_node("x7control-voice", dump)["id"] == 7


def test_surround_conf_uses_sofa_and_targets_eq():
    text = pw.surround_conf_text("/usr/share/libmysofa/default.sofa")
    assert text.count("spatializer") == 7
    assert 'target.object  = "%s"' % pw.EQ_NODE in text
    assert 'node.name      = "%s"' % pw.SURROUND_NODE in text


def test_loopback_command_validates_names():
    argv = pw.loopback_command("x7control-voice", "alsa_output.usb-Creative_Technology_Ltd_Sound_Blaster_X7_0000013O-00.analog-stereo")
    assert argv[0] == "pw-loopback"
    with pytest.raises(ValueError):
        pw.loopback_command("x7control-voice", "sink; rm -rf /")


def test_node_props_params_merges_every_props_object():
    node = {"info": {"params": {"Props": [
        {"volume": 1.0, "params": ["channelmix.disable", False]},
        {"params": ["preamp:Mult", 0.5754, "eq1:Freq", 105.0, "eq1:Gain", -2.1]},
    ]}}}
    p = pw.node_props_params(node)
    assert p["eq1:Freq"] == 105.0 and p["preamp:Mult"] == 0.5754 and p["channelmix.disable"] is False


def test_defaults_and_nodes_from_dump():
    dump = [
        {"id": 1, "type": "PipeWire:Interface:Node", "info": {"props": {"node.name": "alsa_output.usb-Creative_Technology_Ltd_Sound_Blaster_X7_X000013O-00.analog-stereo",
                                                                        "media.class": "Audio/Sink", "node.description": "Sound Blaster X7"}}},
        {"id": 2, "type": "PipeWire:Interface:Node", "info": {"props": {"node.name": "alsa_input.usb-Mic-00.mono-fallback", "media.class": "Audio/Source"}}},
        {"id": 3, "type": "PipeWire:Interface:Metadata", "props": {"metadata.name": "default"},
         "metadata": [{"key": "default.audio.sink", "value": {"name": "alsa_output.usb-Creative_Technology_Ltd_Sound_Blaster_X7_X000013O-00.analog-stereo"}},
                      {"key": "default.audio.source", "value": {"name": "alsa_input.usb-Mic-00.mono-fallback"}}]},
    ]
    assert pw.x7_sink(dump)["id"] == 1
    assert [s["name"] for s in pw.sources(dump)] == ["alsa_input.usb-Mic-00.mono-fallback"]
    assert pw.defaults(dump) == {"sink": "alsa_output.usb-Creative_Technology_Ltd_Sound_Blaster_X7_X000013O-00.analog-stereo",
                                 "source": "alsa_input.usb-Mic-00.mono-fallback"}
