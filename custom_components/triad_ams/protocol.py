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


_SRC = re.compile(r"Out\[(\d+)\]\s+Input Source\s*:\s*(.+)", re.I)
_VOL = re.compile(r"Out\[(\d+)\]\s+Volume\s*:\s*(-?\d+(?:\.\d+)?)", re.I)
_MUTE = re.compile(r"Out\[(\d+)\]\s+Mute status\s*:\s*(\w+)", re.I)
_INPUT = re.compile(r"input\s+(\d+)", re.I)


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
