"""Creative SoundCore control protocol for the Sound Blaster X7 over Bluetooth RFCOMM.

Frame: 0x5A, command id, payload length, payload.  Decoded from the Creative Android app
com.creative.central.x7 (CtSoundCoreManager / SoundCoreDefs / etParam) plus live probing.

DSP parameters ("Malcolm" modules): GET  cmd 0x11 [count, (module, id)...]
                                  reply cmd 0x11 [count, 0, (module, id, float32 LE)...]
                                  SET  cmd 0x12 [count, (module, id, float32 LE)...] -> ack 0x02 [0x12, status]

Everything the box sends is treated as untrusted input: every reply is length-checked
before it is unpacked, and the receive buffer is bounded.
"""
import queue
import re
import socket
import struct
import threading
import time

START = 0x5A
RFCOMM_CHANNEL = 1
MAX_FRAME = 3 + 255         # header + the longest payload a one-byte length field allows
MAC_RE = re.compile(r"[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}")

CMD_ACK = 0x02
CMD_FEATURE_SUPPORT = 0x05
CMD_DEVICE_INFO = 0x07
CMD_COMMIT = 0x08
CMD_GET_PARAM = 0x11
CMD_SET_PARAM = 0x12
CMD_GET_PARAM_DEFAULT = 0x13
CMD_AUDIO_LEVEL_RANGES = 0x22
CMD_AUDIO_LEVEL = 0x23
CMD_AUDIO_MUTE = 0x24
CMD_HW_BUTTON = 0x26
CMD_SPEAKER_CONFIG = 0x29
CMD_FEATURE_CONTROL = 0x39

GET, SET = 1, 0

MODULE_PLAYBACK = 150   # SBX effects, graphic EQ, bass management, master volume/mute
MODULE_VOICE = 149      # CrystalVoice (mic) processing
MODULE_MASTER = 128     # master control manager

# etParam (module 150)
P = {
    "surround_enable": 0, "surround_level": 1,
    "dialogplus_enable": 2, "dialogplus_level": 3,
    "smartvol_enable": 4, "smartvol_level": 5, "smartvol_mode": 6,
    "crystalizer_enable": 7, "crystalizer_level": 8,
    "eq_enable": 9, "eq_preamp": 10,
    "eq_band0": 11, "eq_band1": 12, "eq_band2": 13, "eq_band3": 14, "eq_band4": 15,
    "eq_band5": 16, "eq_band6": 17, "eq_band7": 18, "eq_band8": 19, "eq_band9": 20,
    "bassmgt_enable": 21, "bassmgt_freq": 22,
    "bass_freq": 23, "bass_enable": 24, "bass_level": 25,
    "bassmgt_size_flfr": 26, "bassmgt_size_fclfe": 27, "bassmgt_size_rlrr": 28, "bassmgt_size_slsr": 29,
    "subwoofer_boost": 30,
    "master_volume": 57, "master_mute": 58,
    "speaker_configuration": 61, "line_noise_reduction": 62,
}
EQ_BAND_HZ = ["31", "62", "125", "250", "500", "1K", "2K", "4K", "8K", "16K"]
SMARTVOL_MODES = ["Normal", "Loud", "Night"]

# etParamVIP (module 149)
V = {
    "aec_enable": 0,
    "nr_enable": 4, "nr_level": 5,
    "focus_enable": 6, "focus_mic_distance": 7, "focus_wedge_angle": 8, "focus_source_angle": 9,
    "fx_enable": 10,
    "miceq_enable": 19,
    "micsvm_enable": 44, "micsvm_level": 45,
    "reverb_enable": 46, "reverb_preset": 47,
}
MICEQ_GAIN_IDS = list(range(20, 28))

# hardware buttons (cmd 0x26): bit = id - 1 in the state mask
BTN_SBX, BTN_MUTE, BTN_CRYSTALVOICE = 1, 8, 17
BTN_NAMES = {BTN_SBX: "SBX", BTN_MUTE: "mute", BTN_CRYSTALVOICE: "CrystalVoice"}

# feature control switches (cmd 0x39): bit = ordinal
FEATURES = {
    "aac": 0, "wideband_bt": 1, "anc": 2, "restore_default": 3, "siren": 4, "direct": 5,
    "hp_high_gain": 6, "spdif_in_direct": 7, "psu_high_wattage": 8, "hires_usb": 9,
    "hrtf_speaker": 10, "auto_sleep": 11,
}

SPK_TOGGLE_TO_SPEAKER = -(2 ** 31)
SPK_HEADPHONES, SPK_STEREO, SPK_SURROUND_51 = 1, 2, 4
SPK_NAMES = {1: "Headphones", 2: "Speakers 2.0", 3: "Speakers multi-channel", 4: "Speakers 5.1"}


class X7Error(Exception):
    pass


def valid_mac(mac):
    return isinstance(mac, str) and MAC_RE.fullmatch(mac) is not None


def frame(cmd, payload):
    payload = bytes(payload)
    if not 0 <= cmd <= 255:
        raise ValueError("command id out of range")
    if len(payload) > 255:
        raise ValueError("payload too long for a SoundCore frame")
    return bytes([START, cmd, len(payload)]) + payload


def parse_frames(buf):
    """Split a byte buffer into complete (cmd, payload) frames. Returns (frames, remainder).

    Bytes before a start marker are discarded; an incomplete trailing frame is kept. The
    remainder is therefore never longer than one frame (MAX_FRAME), whatever the box sends.
    """
    frames = []
    while len(buf) >= 3:
        if buf[0] != START:
            buf = buf[1:]
            continue
        cmd, ln = buf[1], buf[2]
        if len(buf) < 3 + ln:
            break
        frames.append((cmd, bytes(buf[3:3 + ln])))
        buf = buf[3 + ln:]
    return frames, buf


def _need(rp, n, what):
    if len(rp) < n:
        raise X7Error("Short reply to %s (%d bytes)" % (what, len(rp)))


def decode_params(rp):
    """Decode a GET_PARAM reply into {id: float}. Malformed trailing data is ignored."""
    out = {}
    if len(rp) < 2:
        return out
    n = rp[0]
    pos = 2
    for _ in range(n):
        if pos + 6 > len(rp):
            break
        pid = rp[pos + 1]
        out[pid] = struct.unpack("<f", rp[pos + 2:pos + 6])[0]
        pos += 6
    return out


def decode_buttons(mask):
    return {b: bool(mask >> (b - 1) & 1) for b in (BTN_SBX, BTN_MUTE, BTN_CRYSTALVOICE)}


def explain_oserror(e):
    code = getattr(e, "errno", None)
    if code == 13:
        return "The X7 refused the control channel (pairing lost). Put it in pairing mode and pair again."
    if code == 112:
        return "X7 not reachable. Is it powered on?"
    if code == 104:
        return "The X7 closed the connection."
    if code == 111:
        return "The X7 refused the connection."
    return str(e)


class X7Client:
    """Threaded RFCOMM client. Public methods block the caller until the box answers."""

    def __init__(self, mac, on_event=None, on_connection=None):
        if not valid_mac(mac):
            raise X7Error("Invalid Bluetooth address: %r" % (mac,))
        self.mac = mac.upper()
        self.on_event = on_event            # unsolicited frames: fn(cmd, payload)
        self.on_connection = on_connection  # fn(connected: bool, message: str)
        self.sock = None
        self.lock = threading.Lock()
        self.replies = queue.Queue()
        self.reader = None
        self._closing = False

    # ---- connection -------------------------------------------------------
    @property
    def connected(self):
        return self.sock is not None

    def connect(self, timeout=8):
        self.close()
        s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        s.settimeout(timeout)
        try:
            s.connect((self.mac, RFCOMM_CHANNEL))
        except OSError as e:
            s.close()
            raise X7Error(explain_oserror(e)) from e
        s.settimeout(0.3)
        self.sock = s
        self._closing = False
        self.reader = threading.Thread(target=self._read_loop, daemon=True, name="x7-rfcomm")
        self.reader.start()
        if self.on_connection:
            self.on_connection(True, "Connected to %s" % self.mac)

    def close(self):
        self._closing = True
        s, self.sock = self.sock, None
        if s:
            try:
                s.close()
            except OSError:
                pass
        if self.reader and self.reader is not threading.current_thread():
            self.reader.join(timeout=1)
        self.reader = None

    def _read_loop(self):
        buf = b""
        sock = self.sock
        while not self._closing and sock is self.sock:
            try:
                chunk = sock.recv(256)
                if not chunk:
                    raise OSError(104, "Connection reset by peer")
            except TimeoutError:
                continue
            except OSError as e:
                if not self._closing:
                    self.sock = None
                    if self.on_connection:
                        self.on_connection(False, explain_oserror(e))
                return
            frames, buf = parse_frames(buf + chunk)
            for f in frames:
                self.replies.put(f)

    # ---- low level --------------------------------------------------------
    def request(self, cmd, payload, expect_cmd=None, expect_ack=False, timeout=2.0):
        """Send one frame and wait for its reply (or ack). Other frames go to on_event."""
        sock = self.sock
        if not sock:
            raise X7Error("Not connected")
        with self.lock:
            # drain stale frames first
            while True:
                try:
                    self._dispatch(self.replies.get_nowait())
                except queue.Empty:
                    break
            try:
                sock.send(frame(cmd, payload))
            except OSError as e:
                raise X7Error(explain_oserror(e)) from e
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                try:
                    rc, rp = self.replies.get(timeout=max(0.01, end - time.monotonic()))
                except queue.Empty:
                    break
                if expect_ack and rc == CMD_ACK and len(rp) >= 2 and rp[0] == cmd:
                    if rp[1] != 0:
                        raise X7Error("Command 0x%02x rejected (status %d)" % (cmd, rp[1]))
                    return rp
                if expect_cmd is not None and rc == expect_cmd:
                    return rp
                if expect_cmd is None and not expect_ack:
                    return rp
                self._dispatch((rc, rp))
            raise X7Error("No reply to command 0x%02x" % cmd)

    def _dispatch(self, item):
        if self.on_event:
            try:
                self.on_event(*item)
            except Exception:  # noqa: BLE001, S110 - a listener bug must not kill the reader
                pass

    # ---- DSP parameters ---------------------------------------------------
    def get_params(self, module, ids):
        out = {}
        ids = list(ids)
        for i in range(0, len(ids), 8):
            chunk = ids[i:i + 8]
            payload = [len(chunk)]
            for pid in chunk:
                payload += [module, pid]
            rp = self.request(CMD_GET_PARAM, payload, expect_cmd=CMD_GET_PARAM)
            out.update(decode_params(rp))
        return out

    def get_param(self, module, pid):
        return self.get_params(module, [pid]).get(pid)

    def set_param(self, module, pid, value):
        payload = [1, module, pid] + list(struct.pack("<f", float(value)))
        self.request(CMD_SET_PARAM, payload, expect_ack=True)

    def set_params(self, module, values):
        items = list(values.items())
        for i in range(0, len(items), 6):
            chunk = items[i:i + 6]
            payload = [len(chunk)]
            for pid, val in chunk:
                payload += [module, pid] + list(struct.pack("<f", float(val)))
            self.request(CMD_SET_PARAM, payload, expect_ack=True)

    def commit(self):
        self.request(CMD_COMMIT, [], expect_ack=True)

    # ---- speaker / buttons / features ------------------------------------
    def get_speaker_config(self):
        rp = self.request(CMD_SPEAKER_CONFIG, [GET], expect_cmd=CMD_SPEAKER_CONFIG)
        _need(rp, 5, "speaker config")
        return struct.unpack("<i", rp[1:5])[0]

    def set_speaker_config(self, value):
        self.request(CMD_SPEAKER_CONFIG, [SET] + list(struct.pack("<i", int(value))), expect_ack=True)

    def get_buttons(self):
        rp = self.request(CMD_HW_BUTTON, [GET], expect_cmd=CMD_HW_BUTTON)
        _need(rp, 5, "button state")
        return decode_buttons(struct.unpack("<I", rp[1:5])[0])

    def set_button(self, btn, state):
        self.request(CMD_HW_BUTTON, [SET, int(btn), 1 if state else 0], expect_ack=True)

    def get_features(self):
        en = self.request(CMD_FEATURE_CONTROL, [1], expect_cmd=CMD_FEATURE_CONTROL)
        av = self.request(CMD_FEATURE_CONTROL, [5], expect_cmd=CMD_FEATURE_CONTROL)
        _need(en, 5, "feature state")
        _need(av, 5, "feature availability")
        enabled = struct.unpack("<I", en[1:5])[0]
        avail = struct.unpack("<I", av[1:5])[0]
        return enabled, avail

    def set_feature(self, name, state):
        self.request(CMD_FEATURE_CONTROL, [SET, FEATURES[name], 1 if state else 0], expect_ack=True)

    # ---- master volume (dB, 1/256 steps; matches the USB ALSA control) ----
    def get_volume_db(self, index=0):
        rp = self.request(CMD_AUDIO_LEVEL, [GET, index], expect_cmd=CMD_AUDIO_LEVEL)
        _need(rp, 3, "volume")
        return struct.unpack("<h", rp[1:3])[0] / 256.0

    def set_volume_db(self, db, index=0):
        raw = int(round(max(-64.0, min(0.0, float(db))) * 256))
        self.request(CMD_AUDIO_LEVEL, [SET, index] + list(struct.pack("<h", raw)), expect_ack=True)

    def get_audio_mute(self, index=0):
        rp = self.request(CMD_AUDIO_MUTE, [GET, index], expect_cmd=CMD_AUDIO_MUTE)
        return bool(rp[1]) if len(rp) > 1 else False

    def set_audio_mute(self, state, index=0):
        self.request(CMD_AUDIO_MUTE, [SET, index, 1 if state else 0], expect_ack=True)
