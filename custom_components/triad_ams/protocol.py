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
from dataclasses import dataclass, field
from itertools import pairwise

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

# Every value measured on output 24 of the real unit with tools/triad_volume_scan.py (2026-10-31): (value, dB).
# A unit whose curve differs is still exact: the client learns each value's dB from the amp's SET replies.
VOLUME_POINTS: list[tuple[int, float]] = [
    (0, -108.5), (1, -100.3), (2, -92.7), (3, -85.8), (4, -79.5), (5, -73.9), (6, -69.0), (7, -64.6),
    (8, -61.0), (9, -58.0), (10, -55.6), (11, -53.9), (12, -52.0), (13, -50.5), (14, -49.6), (15, -48.7),
    (16, -47.7), (17, -46.8), (18, -45.9), (19, -45.0), (20, -44.1), (21, -43.2), (22, -42.3), (23, -41.4),
    (24, -40.6), (25, -39.7), (26, -38.9), (27, -38.0), (28, -37.2), (29, -36.4), (30, -35.6), (31, -34.8),
    (32, -34.0), (33, -33.2), (34, -32.4), (35, -31.7), (36, -30.9), (37, -30.2), (38, -29.4), (39, -28.7),
    (40, -28.0), (41, -27.2), (42, -26.5), (43, -25.8), (44, -25.1), (45, -24.5), (46, -23.8), (47, -23.1),
    (48, -22.5), (49, -21.8), (50, -21.2), (51, -20.5), (52, -19.9), (53, -19.3), (54, -18.7), (55, -18.1),
    (56, -17.5), (57, -16.9), (58, -16.4), (59, -15.8), (60, -15.3), (61, -14.7), (62, -14.2), (63, -13.7),
    (64, -13.1), (65, -12.6), (66, -12.1), (67, -11.6), (68, -11.1), (69, -10.7), (70, -10.2), (71, -9.7),
    (72, -9.3), (73, -8.9), (74, -8.4), (75, -8.0), (76, -7.6), (77, -7.2), (78, -6.8), (79, -6.4),
    (80, -6.0), (81, -5.6), (82, -5.3), (83, -4.9), (84, -4.6), (85, -4.2), (86, -3.9), (87, -3.6),
    (88, -3.3), (89, -3.0), (90, -2.7), (91, -2.4), (92, -2.1), (93, -1.8), (94, -1.6), (95, -1.3),
    (96, -1.1), (97, -0.9), (98, -0.6), (99, -0.4), (100, 0.0),
]


VOLUME_MATCH_DB = 0.05                  # the amp reports dB with one decimal


class VolumeCurve:
    """Amp volume value (0..100) <-> dB.

    Starts from the measured VOLUME_POINTS and learns the exact dB of every value the amp reports in a
    'Set Out[n] Output Volume to <dB>' reply. A dB that matches a known point maps back to exactly that
    value, so a volume Home Assistant wrote reads back as the same value (no drift on turn on / steps).
    """

    def __init__(self, learned: dict[int, float] | None = None) -> None:
        self.learned: dict[int, float] = {}
        self.dirty = False
        for value, db in (learned or {}).items():
            self.learn(int(value), float(db))
        self.dirty = False

    def learn(self, value: int, db: float) -> None:
        if 0 <= value <= VOLUME_MAX and self.learned.get(value) != db:
            self.learned[value] = db
            self.dirty = True

    def points(self) -> list[tuple[int, float]]:
        merged = dict(VOLUME_POINTS)
        merged.update(self.learned)
        return sorted(merged.items())

    def to_db(self, value: int) -> float:
        value = max(0, min(VOLUME_MAX, int(value)))
        points = self.points()
        for (v0, d0), (v1, d1) in pairwise(points):
            if v0 <= value <= v1:
                return d0 + (d1 - d0) * (value - v0) / (v1 - v0)
        return 0.0

    def to_value(self, db: float) -> int:
        points = self.points()
        nearest = min(points, key=lambda p: abs(p[1] - db))
        if abs(nearest[1] - db) <= VOLUME_MATCH_DB:
            return nearest[0]
        if db <= points[0][1]:
            return points[0][0]
        if db >= points[-1][1]:
            return VOLUME_MAX
        for (v0, d0), (v1, d1) in pairwise(points):
            if d0 <= db <= d1:
                return round(v0 + (v1 - v0) * (db - d0) / (d1 - d0))
        return 0


_DEFAULT_CURVE = VolumeCurve()


def value_to_db(value: int) -> float:
    """Amp volume value (0..100) -> dB, from the measured points only."""
    return _DEFAULT_CURVE.to_db(value)


def db_to_value(db: float) -> int:
    """dB -> nearest amp volume value (0..100), from the measured points only."""
    return _DEFAULT_CURVE.to_value(db)


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
_SET_VOL = re.compile(r"Out\[(\d+)\]\s+Output Volume to\s*(-?\d+(?:\.\d+)?)", re.I)


@dataclass
class OutputState:
    """What the amp reports for one output."""
    source: int | None = None       # 1..24, None = Audio Off
    volume_db: float | None = None
    muted: bool | None = None
    volume_value: int | None = None     # 0..100, filled from volume_db by the client's VolumeCurve
    answered: set[str] = field(default_factory=set, compare=False)     # which of source / volume / mute the amp answered

    @property
    def complete(self) -> bool:
        return {"source", "volume", "mute"} <= self.answered


def _for_output(match: re.Match, output: int | None) -> bool:
    return output is None or int(match.group(1)) == output


def parse_reply(text: str, state: OutputState, output: int | None = None) -> bool:
    """Fold one reply into state. Returns True if the reply was understood (and is about output, if given)."""
    text = text.replace("\x00", " ").strip()
    match = _SRC.search(text)
    if match:
        if not _for_output(match, output):
            return False
        found = _INPUT.search(match.group(2))
        state.source = int(found.group(1)) if found else None
        state.answered.add("source")
        return True
    match = _VOL.search(text)
    if match:
        if not _for_output(match, output):
            return False
        state.volume_db = float(match.group(2))
        state.answered.add("volume")
        return True
    match = _MUTE.search(text)
    if match:
        if not _for_output(match, output):
            return False
        state.muted = match.group(2).lower() == "mute"
        state.answered.add("mute")
        return True
    return False


def parse_set_volume(text: str) -> tuple[int, float] | None:
    """'Set Out[n] Output Volume to -13.1' -> (n, -13.1)."""
    match = _SET_VOL.search(text.replace("\x00", " "))
    return (int(match.group(1)), float(match.group(2))) if match else None


def is_error(text: str) -> bool:
    return "command error" in text.lower()


def parse_eq(kind: str, text: str, output: int | None = None) -> float | None:
    """Parse a Get/Set reply for a tone control into dB (balance: + right, - left). None if not understood.

    Bass / treble replies look like 'Get Out[2] Bass : -3' (and 'Set Out[2] Bass to 3').
    Balance replies look like 'Bal Center', 'Bal R 1' or 'Bal L 4'.
    """
    text = text.replace("\x00", " ").strip()
    match = _EQ.search(text)
    if not match or match.group(2).lower() != kind or not _for_output(match, output):
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
