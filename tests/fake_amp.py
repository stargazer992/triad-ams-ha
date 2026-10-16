"""A simulated Triad AMS that speaks the protocol verified on the real unit (docs/protocol.md)."""
from __future__ import annotations

import asyncio
import bisect
import time

VOLUME_POINTS = [(0, -108.5), (8, -61.0), (16, -47.7), (32, -34.0), (48, -22.5), (64, -13.1), (72, -9.3), (80, -6.0), (88, -3.3), (96, -1.1), (100, 0.0)]


def _db(value: int) -> float:
    xs = [p[0] for p in VOLUME_POINTS]
    i = min(bisect.bisect_right(xs, value), len(xs) - 1)
    (v0, d0), (v1, d1) = VOLUME_POINTS[i - 1], VOLUME_POINTS[i]
    return d0 + (d1 - d0) * (value - v0) / (v1 - v0) if v1 != v0 else d1


def _fmt(db: float) -> str:
    return "0" if db == 0 else f"{db:.1f}"


class FakeAmp:
    def __init__(self) -> None:
        self.source = {n: 0x18 for n in range(24)}        # value 0x18 = Audio Off
        self.volume = {n: 0 for n in range(24)}
        self.muted = {n: False for n in range(24)}
        self.connections = 0
        self.frames: list[bytes] = []
        self.one_shot = False                              # True = hang up after every reply (worst-case firmware)
        self.ignore_mute_after_source = 0.0               # seconds; a (un)mute this soon after a source change is acknowledged but ignored
        self._source_changed: dict[int, float] = {}
        self.eq = {(c, n): 0x18 for c in (0x2F, 0x30, 0x31) for n in range(24)}   # bass / treble / balance, 0x18 = flat
        self.silent = False                                # True = accept but never answer
        self._server: asyncio.AbstractServer | None = None
        self.port = 0

    @staticmethod
    def _eq_name(cmd: int) -> str:
        return {0x2F: "Bass", 0x30: "Treble", 0x31: "Balance"}[cmd]

    @staticmethod
    def _eq_text(cmd: int, value: int) -> str:
        db = (value - 0x18) / 2
        if cmd == 0x31:
            return "Bal Center" if db == 0 else f"Bal {'R' if db > 0 else 'L'} {abs(db):g}"
        return f"{db:g}"

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    def reply(self, frame: bytes) -> str:
        if len(frame) < 6 or frame[:4] != bytes([0xFF, 0x55, frame[2], 0x03]):
            return "Command error"
        cmd = frame[4]
        is_get = len(frame) == 7 and frame[5] == 0xF5
        out = frame[6] if is_get else frame[5]
        if out >= 24:
            return "Command error"
        n = out + 1
        if is_get:
            if cmd == 0x1D:
                s = self.source[out]
                return f"Get Out[{n}] Input Source : " + ("Audio Off" if s == 0x18 else f"input {s + 1}")
            if cmd == 0x1E:
                return f"Get Out[{n}] Volume : {_fmt(_db(self.volume[out]))}"
            if cmd == 0x17:
                return f"Get Out[{n}] Mute status : " + ("mute" if self.muted[out] else "Unmute")
            if cmd in (0x2F, 0x30, 0x31):
                return f"Get Out[{n}] {self._eq_name(cmd)} : {self._eq_text(cmd, self.eq[(cmd, out)])}"
            return "Command error"
        value = frame[6] if len(frame) > 6 else 0
        if cmd in (0x2F, 0x30, 0x31):
            if value > 0x30:
                return "Command error"
            self.eq[(cmd, out)] = value
            return f"Set Out[{n}] {self._eq_name(cmd)} to {self._eq_text(cmd, value)}"
        if cmd == 0x1D:
            if value > 0x18:
                return "Command error"
            self.source[out] = value
            self._source_changed[out] = time.monotonic()
            return f"Set Out[{n}] Input Source to " + ("Audio Off" if value == 0x18 else f"input {value + 1}")
        if cmd == 0x1E:
            if value > 100:
                return "Command error"
            self.volume[out] = value
            self.muted[out] = False                        # setting the volume clears mute (verified)
            return f"Set Out[{n}] Output Volume to {_fmt(_db(value))}"
        if cmd in (0x17, 0x18):
            recent = time.monotonic() - self._source_changed.get(out, -1e9) < self.ignore_mute_after_source
            if not recent:
                self.muted[out] = cmd == 0x17
            return f"Set Out[{n}] " + ("Mute" if cmd == 0x17 else "Unmute")
        if cmd == 0x16:
            self.volume[out] = max(0, self.volume[out] - 3)
            return f"Set Out[{n}] Volume -3db"
        return "Command error"

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        try:
            while True:
                head = await reader.readexactly(4)
                # FF 55 04 03 cmd F5 out | FF 55 04 03 cmd out val  (7 bytes)  and  FF 55 03 03 cmd out  (6 bytes)
                frame = head + await reader.readexactly(3 if head[2] == 4 else 2)
                self.frames.append(frame)
                if self.silent:
                    continue
                writer.write(self.reply(frame).encode("latin-1") + b"\x00")
                await writer.drain()
                if self.one_shot:
                    break
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
