import json

from x7control import config


def test_clean_name_strips_control_and_markup():
    assert config.clean_name('My "EQ"\n{x}') == "My EQx"
    assert config.clean_name("") == "preset"
    assert len(config.clean_name("a" * 100)) == 40


def test_clean_pc_preset_clamps_and_fills():
    p = config.clean_pc_preset({"name": "x", "preamp": -99, "bands": [{"type": "evil", "freq": 1e9, "q": "nan", "gain": -100}]})
    assert p["preamp"] == -30
    assert len(p["bands"]) == 10
    assert p["bands"][0] == {"type": "peaking", "freq": 20000, "q": 1.0, "gain": -30}
    assert config.clean_pc_preset("garbage") is None
    assert config.clean_pc_preset({"bands": "nope"}) is None


def test_clean_box_preset():
    p = config.clean_box_preset({"name": "b", "preamp": 3, "bands": [30, -30, "x"]})
    assert p["bands"][:3] == [24, -24, 0.0] and len(p["bands"]) == 10


def test_load_survives_garbage(tmp_path, monkeypatch):
    f = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_FILE", str(f))
    monkeypatch.setattr(config, "LEGACY_X7CTL_CONFIG", str(tmp_path / "none.json"))
    f.write_text("{ not json")
    assert config.load()["mac"] is None
    f.write_text(json.dumps({"mac": "rm -rf /", "pc_eq_presets": [1, 2], "voice_source": "bad name!", "pc_eq_enabled": 0}))
    cfg = config.load()
    assert cfg["mac"] is None
    assert cfg["pc_eq_presets"] == []
    assert cfg["voice_source"] is None
    assert cfg["pc_eq_enabled"] is False
    f.write_text(json.dumps({"mac": "00:02:3c:55:3a:f6", "voice_source": "alsa_input.usb-Foo_Bar-00.mono-fallback"}))
    cfg = config.load()
    assert cfg["mac"] == "00:02:3C:55:3A:F6"
    assert cfg["voice_source"] == "alsa_input.usb-Foo_Bar-00.mono-fallback"


def test_legacy_x7ctl_mac_is_picked_up(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_FILE", str(tmp_path / "config.json"))
    legacy = tmp_path / "x7ctl.json"
    legacy.write_text(json.dumps({"mac": "AA:BB:CC:DD:EE:FF"}))
    monkeypatch.setattr(config, "LEGACY_X7CTL_CONFIG", str(legacy))
    assert config.load()["mac"] == "AA:BB:CC:DD:EE:FF"


def test_save_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path / "x7control"))
    monkeypatch.setattr(config, "CONFIG_FILE", str(tmp_path / "x7control" / "config.json"))
    monkeypatch.setattr(config, "LEGACY_X7CTL_CONFIG", str(tmp_path / "none.json"))
    cfg = config.load()
    cfg["mac"] = "AA:BB:CC:DD:EE:FF"
    cfg["box_eq_presets"] = [{"name": "Bassy", "preamp": -2, "bands": [6] * 10}]
    config.save(cfg)
    again = config.load()
    assert again["mac"] == "AA:BB:CC:DD:EE:FF"
    assert again["box_eq_presets"][0]["name"] == "Bassy"
