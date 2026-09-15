import json

import pytest

from x7control import config, pipewire as pw

OPTICAL = "alsa_output.pci-0000_0e_00.4.iec958-stereo"


def test_slug_for_is_safe_and_stable():
    assert pw.slug_for(OPTICAL) == "pci-0000-0e-00-4-iec958-stereo"
    assert pw.slug_for("weird/../name with spaces") == "weird-name-with-spaces"
    assert pw.slug_for("") == "output"
    assert len(pw.slug_for("x" * 500)) <= 60


def test_eq_paths_and_nodes_per_slug():
    assert pw.eq_node_name("") == "effect_input.x7control-eq"
    assert pw.eq_node_name("desk") == "effect_input.x7control-eq-desk"
    assert pw.eq_conf_path("").endswith("x7control-headphone-eq.conf")
    assert pw.eq_conf_path("desk").endswith("x7control-eq-desk.conf")


def test_eq_target_x7_by_card_name_others_by_node_name():
    assert pw.eq_target_for("alsa_output.usb-Creative_Technology_Ltd_Sound_Blaster_X7_0000013O-00.analog-stereo") == {"alsa.card_name": "Sound Blaster X7"}
    assert pw.eq_target_for(OPTICAL) == {"node.name": OPTICAL}


def test_eq_conf_for_an_output_targets_its_node():
    text = pw.eq_conf_text(pw.FLAT_PRESET, "desk", {"node.name": OPTICAL}, 'Desk "monitors" EQ')
    assert 'node.name           = "effect_input.x7control-eq-desk"' in text
    assert 'node.name    = "effect_output.x7control-eq-desk"' in text
    assert 'filter.smart.name   = "x7control-eq-desk"' in text
    assert 'node.name = "%s"' % OPTICAL in text
    assert 'node.description = "Desk \\"monitors\\" EQ"' in text
    with pytest.raises(ValueError):
        pw.eq_conf_text(pw.FLAT_PRESET, "desk", {"node.name": 'x" } context.exec'})
    with pytest.raises(ValueError):
        pw.eq_conf_text(pw.FLAT_PRESET, "desk", {"media.name": "x"})


def test_x7_eq_conf_unchanged_by_default():
    text = pw.eq_conf_text(pw.HD598CS_PRESET)
    assert 'alsa.card_name = "Sound Blaster X7"' in text
    assert 'node.name           = "effect_input.x7control-eq"' in text


def test_outputs_conf_only_writes_validated_values():
    outs = {
        OPTICAL: {"label": "Desk speakers (TubeMagic D1)", "format": "S24_LE", "rate": "96000", "dither": "wannamaker3", "no_suspend": True},
        "alsa_output.pci-0000_0c_00.1.hdmi-stereo": {"label": "", "format": "EVIL", "rate": "1234", "dither": "x", "no_suspend": False},
        "bad name!": {"label": "nope"},
    }
    text = pw.outputs_conf_text(outs)
    assert text.count("matches") == 1
    assert 'node.description = "Desk speakers (TubeMagic D1)"' in text
    assert "audio.format = S24_LE" in text and "audio.rate = 96000" in text and "dither.method = wannamaker3" in text
    assert "session.suspend-timeout-seconds = 0" in text
    assert "EVIL" not in text and "1234" not in text and "nope" not in text


def test_config_outputs_roundtrip_and_cleaning(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "LEGACY_X7CTL_CONFIG", str(tmp_path / "none.json"))
    (tmp_path / "config.json").write_text(json.dumps({"outputs": {
        OPTICAL: {"label": "Desk\n{speakers}", "format": "S24_LE", "rate": 96000, "dither": "bogus", "eq_enabled": 0},
        "bad name!": {"label": "x"},
        "alsa_output.ok": "not a dict",
    }}))
    cfg = config.load()
    assert list(cfg["outputs"]) == [OPTICAL]
    o = cfg["outputs"][OPTICAL]
    assert o["label"] == "Deskspeakers" and o["format"] == "S24_LE" and o["rate"] == "96000" and o["dither"] == "" and o["eq_enabled"] is False
    config.save(cfg)
    assert config.load()["outputs"][OPTICAL]["rate"] == "96000"


def test_clean_output_defaults_are_auto_not_none():
    o = config.clean_output({})
    assert o == {"label": "", "format": "", "rate": "", "dither": "", "no_suspend": False, "eq_enabled": True}
    assert config.clean_output({"rate": None})["rate"] == ""
    assert config.clean_output({"rate": 96000})["rate"] == "96000"


def test_sink_format_from_dump():
    node = {"info": {"params": {"Format": [{"format": "S16LE", "rate": 48000}]}}}
    assert pw.sink_format(node) == ("S16LE", 48000)
    assert pw.sink_format({"info": {}}) == (None, None)
