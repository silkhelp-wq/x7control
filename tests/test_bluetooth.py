from x7control import bluetooth as bt
from x7control import soundcore as sc


def test_choose_x7_prefers_creative_address_over_lookalike_names():
    lines = [
        "Device AA:BB:CC:DD:EE:01 X7 Lookalike",
        "Device 00:02:3C:55:3A:F6 Sound Blaster X7",
    ]
    assert bt.choose_x7(lines) == ("00:02:3C:55:3A:F6", "Sound Blaster X7")
    assert bt.choose_x7(reversed(lines)) == ("00:02:3C:55:3A:F6", "Sound Blaster X7")


def test_choose_x7_accepts_exact_name_without_creative_oui():
    assert bt.choose_x7(["Device 11:22:33:44:55:66 Sound Blaster X7"]) == ("11:22:33:44:55:66", "Sound Blaster X7")


def test_choose_x7_never_picks_a_random_x7_named_device():
    assert bt.choose_x7(["Device 11:22:33:44:55:66 Redmi X7", "Device ZZ:02:3C:55:3A:F6 Sound Blaster X7"]) == (None, None)


def test_safe_float():
    assert sc.safe_float(float("nan"), 0, 1, 0.5) == 0.5
    assert sc.safe_float(float("inf"), 0, 1, 0.5) == 0.5
    assert sc.safe_float("x", 0, 1, 0.5) == 0.5
    assert sc.safe_float(9e18, 0, 2, 0.5) == 2
    assert sc.safe_float(-3, 0, 2, 0.5) == 0
    assert sc.safe_float("0.25", 0, 1, 0.5) == 0.25
