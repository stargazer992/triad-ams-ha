"""Triad AMS wire protocol (TCP 52000). Pure functions, no Home Assistant imports.

Everything here was verified against a real AMS 24x24 on 2026-10-16 (see docs/protocol.md).

Frames (all start with FF 55 04 03):
    GET   FF 55 04 03 <cmd> F5 <out>          reply: "Get Out[n] ... : value"
    SET   FF 55 04 03 <cmd> <out> <value>     reply: "Set Out[n] ..."
Commands:
    0x1D source   value = input number - 1 (0..23), 0x18 = Audio Off
    0x1E volume   value = 0..100 (a curve, see VOLUME_POINTS); also clears mute
    0x17 mute     value ignored
    0x18 unmute   value ignored
    0x16 volume down 3 dB (value ignored)
    0x2F bass     value = 0x18 + 2*dB  (0x00..0x30 = -12..+12 dB in 0.5 dB steps, 0x18 = flat)
    0x30 treble   same encoding
    0x31 balance  same encoding, 0x18 = centre, above = right, below = left
Replies are plain text, null terminated. Output bytes are zero based (byte 0 = "Out[1]").
"""
from __future__ import annotations

import re
from dataclasses import dataclass

PREFIX = bytes([0xFF, 0x55, 0x04, 0x03])
GET_FLAG = 0xF5

CMD_VOL_DOWN = 0x16
CMD_MUTE = 0x17
CMD_UNMUTE = 0x18
CMD_SOURCE = 0x1D
CMD_VOLUME = 0x1E

CMD_BASS = 0x2F
CMD_TREBLE = 0x30
CMD_BALANCE = 0x31

# Tone controls (v0.2.0). Order is the order they are read and shown.
EQ_KINDS = ("bass", "treble", "balance")
EQ_COMMANDS = {"bass": CMD_BASS, "treble": CMD_TREBLE, "balance": CMD_BALANCE}
EQ_FLAT_BYTE = 0x18                     # 0 dB / centre
EQ_MAX_BYTE = 0x30
EQ_MIN_DB = -12.0
EQ_MAX_DB = 12.0

AUDIO_OFF_VALUE = 0x18                  # source value meaning "Audio Off"
MAX_OUTPUTS = 24
MAX_INPUTS = 24
VOLUME_MAX = 100

# Measured on output 24: (value, dB). Between points the curve is interpolated linearly.
# tools/triad_volume_scan.py measures the whole 0..100 table; paste it here for exact values.
VOLUME_POINTS: list[tuple[int, float]] = [
    (0, -108.5), (8, -61.0), (16, -47.7), (32, -34.0), (48, -22.5), (64, -13.1),
    (72, -9.3), (80, -6.0), (88, -3.3), (96, -1.1), (100, 0.0),
]


def value_to_db(value: int) -> float:
    """Amp volume value (0..100) -> dB."""
    value = max(0, min(VOLUME_MAX, int(value)))
    for (v0, d0), (v1, d1) in zip(VOLUME_POINTS, VOLUME_POINTS[1:]):
        if v0 <= value <= v1:
            return d0 + (d1 - d0) * (value - v0) / (v1 - v0)
    return 0.0


def db_to_value(db: float) -> int:
    """dB -> nearest amp volume value (0..100)."""
    if db <= VOLUME_POINTS[0][1]:
        return 0
    if db >= VOLUME_POINTS[-1][1]:
        return VOLUME_MAX
    for (v0, d0), (v1, d1) in zip(VOLUME_POINTS, VOLUME_POINTS[1:]):
        if d0 <= db <= d1:
            return round(v0 + (v1 - v0) * (db - d0) / (d1 - d0))
    return 0


def get_frame(cmd: int, output: int) -> bytes:
    """output is 1-based (as on the amp's labels)."""
    return PREFIX + bytes([cmd, GET_FLAG, output - 1])


def set_frame(cmd: int, output: int, value: int = 0) -> bytes:
    return PREFIX + bytes([cmd, output - 1, value & 0xFF])


def source_frame(output: int, input_number: int | None) -> bytes:
    """input_number 1..24, or None for Audio Off."""
    value = AUDIO_OFF_VALUE if input_number is None else input_number - 1
    return set_frame(CMD_SOURCE, output, value)


def volume_frame(output: int, value: int) -> bytes:
    return set_frame(CMD_VOLUME, output, max(0, min(VOLUME_MAX, value)))


def mute_frame(output: int, mute: bool) -> bytes:
    return set_frame(CMD_MUTE if mute else CMD_UNMUTE, output, 1 if mute else 0)


def db_to_eq_byte(db: float) -> int:
    """dB (-12..+12, 0.5 steps) -> amp byte. Out of range values are clamped."""
    db = max(EQ_MIN_DB, min(EQ_MAX_DB, float(db)))
    return max(0, min(EQ_MAX_BYTE, round(EQ_FLAT_BYTE + 2 * db)))


def eq_byte_to_db(value: int) -> float:
    return (int(value) - EQ_FLAT_BYTE) / 2


def snap_eq_db(db: float) -> float:
    """The value the amp will really hold for a requested dB."""
    return eq_byte_to_db(db_to_eq_byte(db))


def eq_get_frame(kind: str, output: int) -> bytes:
    return get_frame(EQ_COMMANDS[kind], output)


def eq_set_frame(kind: str, output: int, db: float) -> bytes:
    return set_frame(EQ_COMMANDS[kind], output, db_to_eq_byte(db))


_SRC = re.compile(r"Out\[(\d+)\]\s+Input Source\s*:\s*(.+)", re.I)
_VOL = re.compile(r"Out\[(\d+)\]\s+Volume\s*:\s*(-?\d+(?:\.\d+)?)", re.I)
_MUTE = re.compile(r"Out\[(\d+)\]\s+Mute status\s*:\s*(\w+)", re.I)
_INPUT = re.compile(r"input\s+(\d+)", re.I)
_EQ = re.compile(r"Out\[(\d+)\]\s+(Bass|Treble|Balance)\s*:?\s*(?:to\s+)?(.+)", re.I)
_BAL = re.compile(r"Bal(?:ance)?\s*(Center|Centre|[LR])?\s*(-?\d+(?:\.\d+)?)?", re.I)
_NUM = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass
class OutputState:
    """What the amp reports for one output."""
    source: int | None = None       # 1..24, None = Audio Off
    volume_db: float | None = None
    muted: bool | None = None

    @property
    def volume_value(self) -> int | None:
        return None if self.volume_db is None else db_to_value(self.volume_db)

    @property
    def complete(self) -> bool:
        return self.volume_db is not None and self.muted is not None


def parse_reply(text: str, state: OutputState) -> bool:
    """Fold one reply into state. Returns True if the reply was understood."""
    text = text.replace("\x00", " ").strip()
    match = _SRC.search(text)
    if match:
        found = _INPUT.search(match.group(2))
        state.source = int(found.group(1)) if found else None
        return True
    match = _VOL.search(text)
    if match:
        state.volume_db = float(match.group(2))
        return True
    match = _MUTE.search(text)
    if match:
        state.muted = match.group(2).lower() == "mute"
        return True
    return False


def is_error(text: str) -> bool:
    return "command error" in text.lower()


def parse_eq(kind: str, text: str) -> float | None:
    """Parse a Get/Set reply for a tone control into dB (balance: + right, - left). None if not understood.

    Bass / treble replies look like 'Get Out[2] Bass : -3' (and 'Set Out[2] Bass to 3').
    Balance replies look like 'Bal Center', 'Bal R 1' or 'Bal L 4'.
    """
    text = text.replace("\x00", " ").strip()
    match = _EQ.search(text)
    if not match or match.group(2).lower() != kind:
        return None
    body = match.group(3).strip()
    if kind == "balance":
        bal = _BAL.search(body)
        if not bal:
            return None
        side, num = bal.group(1), bal.group(2)
        if side and side.lower().startswith("c"):
            return 0.0
        if side and num is not None:
            amount = abs(float(num))
            return amount if side.upper() == "R" else -amount
        if num is not None:                      # plain signed number
            return float(num)
        return None
    found = _NUM.search(body)
    return float(found.group(0)) if found else None
