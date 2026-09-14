import pytest

from x7control import eqcurve as eq


def test_peaking_hits_its_gain_at_center_and_fades_away():
    c = eq.biquad_coeffs("peaking", 1000, 1.0, 6.0)
    assert eq.biquad_db(c, 1000) == pytest.approx(6.0, abs=0.05)
    assert abs(eq.biquad_db(c, 20)) < 0.1
    assert abs(eq.biquad_db(c, 19000)) < 0.2


def test_shelves_move_one_side_only():
    lo = eq.biquad_coeffs("lowshelf", 200, 0.7, -4.0)
    assert eq.biquad_db(lo, 20) == pytest.approx(-4.0, abs=0.1)
    assert abs(eq.biquad_db(lo, 10000)) < 0.1
    hi = eq.biquad_coeffs("highshelf", 5000, 0.7, 3.0)
    assert eq.biquad_db(hi, 18000) == pytest.approx(3.0, abs=0.2)
    assert abs(eq.biquad_db(hi, 50)) < 0.1


def test_pass_filters_and_notch():
    assert eq.biquad_db(eq.biquad_coeffs("highpass", 100, 0.7, 0), 20) < -20
    assert abs(eq.biquad_db(eq.biquad_coeffs("highpass", 100, 0.7, 0), 5000)) < 0.1
    assert eq.biquad_db(eq.biquad_coeffs("lowpass", 5000, 0.7, 0), 19000) < -15
    assert eq.biquad_db(eq.biquad_coeffs("notch", 1000, 10, 0), 1000) < -40


def test_response_sums_bands_and_preamp():
    bands = [{"type": "peaking", "freq": 1000, "q": 1.0, "gain": 3.0}, {"type": "peaking", "freq": 1000, "q": 1.0, "gain": 3.0}]
    pts = dict(eq.response(bands, preamp_db=-2.0, freqs=[1000.0, 20.0]))
    assert pts[1000.0] == pytest.approx(4.0, abs=0.1)
    assert pts[20.0] == pytest.approx(-2.0, abs=0.1)
    assert len(eq.response([])) == 240


def test_graphic_bands_and_descriptions():
    gb = eq.graphic_bands([0] * 9 + [6])
    assert gb[9]["freq"] == 16000 and gb[9]["gain"] == 6
    assert "Boosting 6.0 dB" in eq.describe_graphic(9, 6)
    assert eq.describe_graphic(0, 0).startswith("Sub-bass")


def test_describe_band_reads_naturally():
    assert eq.describe_band({"type": "lowshelf", "freq": 105, "q": 0.7, "gain": -2.1}).startswith("Cuts 2.1 dB everything below 105 Hz")
    s = eq.describe_band({"type": "peaking", "freq": 3480, "q": 5.91, "gain": -3.3})
    assert "3.5 kHz" in s and "narrow" in s and "upper mids" in s
    assert eq.describe_band({"type": "highpass", "freq": 80, "q": 0.7, "gain": 5}).startswith("Removes everything below 80 Hz")
    assert eq.describe_band({"type": "peaking", "freq": 500, "q": 1, "gain": 0}).startswith("Off")


def test_zone_lookup_covers_the_whole_range():
    for f in (20, 59, 60, 249, 1000, 3999, 5999, 19999, 25000):
        assert eq.zone_for(f)[0]
    assert eq.fmt_hz(31) == "31 Hz" and eq.fmt_hz(16000) == "16.0 kHz"
