import struct

import pytest

from x7control import soundcore as sc


def test_frame_layout():
    assert sc.frame(0x29, [1]) == b"\x5a\x29\x01\x01"
    assert sc.frame(0x08, []) == b"\x5a\x08\x00"


def test_frame_rejects_oversized_payload():
    with pytest.raises(ValueError):
        sc.frame(0x12, bytes(256))
    with pytest.raises(ValueError):
        sc.frame(0x100, [])


def test_parse_frames_splits_and_resyncs():
    junk = b"\x00\xff" + sc.frame(0x02, [0x12, 0]) + sc.frame(0x29, [1, 1, 0, 0, 0])
    frames, rest = sc.parse_frames(junk + b"\x5a\x11")  # trailing partial frame
    assert frames == [(0x02, b"\x12\x00"), (0x29, b"\x01\x01\x00\x00\x00")]
    assert rest == b"\x5a\x11"


def test_parse_frames_remainder_is_bounded():
    # a max-length frame plus junk plus a partial header: remainder never exceeds one frame
    frames, rest = sc.parse_frames(b"\x5a\x11\xff" + b"\x00" * 255 + b"\x00" * 5000 + b"\x5a\x11")
    assert frames == [(0x11, b"\x00" * 255)]
    assert rest == b"\x5a\x11"
    _, rest = sc.parse_frames(b"\x5a\x11\xff" + b"\x00" * 100)
    assert len(rest) <= sc.MAX_FRAME


def test_decode_params_tolerates_truncation():
    rp = bytes([2, 0, 150, 57]) + struct.pack("<f", -30.0) + bytes([150, 58])  # second entry cut short
    assert sc.decode_params(rp) == {57: -30.0}
    assert sc.decode_params(b"") == {}
    assert sc.decode_params(b"\x05") == {}


def test_decode_buttons():
    mask = (1 << (sc.BTN_SBX - 1)) | (1 << (sc.BTN_CRYSTALVOICE - 1))
    assert sc.decode_buttons(mask) == {sc.BTN_SBX: True, sc.BTN_MUTE: False, sc.BTN_CRYSTALVOICE: True}


def test_valid_mac():
    assert sc.valid_mac("00:02:3C:55:3A:F6")
    assert sc.valid_mac("aa:bb:cc:dd:ee:ff")
    assert not sc.valid_mac("00:02:3C:55:3A")
    assert not sc.valid_mac("00:02:3C:55:3A:F6\n")
    assert not sc.valid_mac("")
    assert not sc.valid_mac(None)


def test_client_rejects_bad_mac():
    with pytest.raises(sc.X7Error):
        sc.X7Client("not a mac")


class FakeSock:
    """Stands in for the RFCOMM socket: records each send and answers it from a script."""

    def __init__(self, client, replies):
        self.client = client
        self.sent = []
        self.replies = list(replies)

    def send(self, data):
        self.sent.append(data)
        if self.replies:
            for f in sc.parse_frames(self.replies.pop(0))[0]:
                self.client.replies.put(f)


def make_client(replies):
    c = sc.X7Client("00:02:3C:55:3A:F6")
    c.sock = FakeSock(c, replies)
    return c


def test_short_reply_is_an_error_not_a_crash():
    c = make_client([sc.frame(sc.CMD_SPEAKER_CONFIG, [1, 1])])  # 2 bytes where 5 are expected
    with pytest.raises(sc.X7Error):
        c.get_speaker_config()


def test_ack_status_nonzero_is_rejected():
    c = make_client([sc.frame(sc.CMD_ACK, [sc.CMD_SET_PARAM, 3])])
    with pytest.raises(sc.X7Error, match="rejected"):
        c.set_param(sc.MODULE_PLAYBACK, 1, 0.5)


def test_volume_roundtrip_and_clamp():
    c = make_client([sc.frame(sc.CMD_AUDIO_LEVEL, [0] + list(struct.pack("<h", -30 * 256)))])
    assert c.get_volume_db() == -30.0
    c = make_client([sc.frame(sc.CMD_ACK, [sc.CMD_AUDIO_LEVEL, 0])])
    c.set_volume_db(+20)  # above 0 dB is clamped
    assert c.sock.sent[-1] == sc.frame(sc.CMD_AUDIO_LEVEL, [sc.SET, 0] + list(struct.pack("<h", 0)))
