"""Async TCP client for the Triad AMS. One short session per refresh or command, never kept open.

The AMS is known to hang up under unfriendly clients and Control4 holds its own connection, so this
client opens a connection only when it has something to say, serialises everything behind one lock,
and closes right after.
"""
from __future__ import annotations

import asyncio
import logging

from . import protocol
from .protocol import OutputState

_LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 4.0
REPLY_TIMEOUT = 2.0
WRITE_SETTLE = 0.25           # pause after each SET frame: a frame sent right after a source change was ignored by the amp


class TriadError(Exception):
    """Could not talk to the amp."""


class TriadClient:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self._lock = asyncio.Lock()

    async def _open(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            return await asyncio.wait_for(asyncio.open_connection(self.host, self.port), CONNECT_TIMEOUT)
        except (OSError, asyncio.TimeoutError) as err:
            raise TriadError(f"Cannot connect to {self.host}:{self.port}: {err}") from err

    @staticmethod
    async def _exchange(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, frame: bytes) -> str:
        """Send one frame and read its null terminated reply. Returns '' if the amp stays silent."""
        writer.write(frame)
        await writer.drain()
        try:
            data = await asyncio.wait_for(reader.readuntil(b"\x00"), REPLY_TIMEOUT)
        except asyncio.TimeoutError:
            return ""
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError) as err:
            raise TriadError(f"Connection lost: {err}") from err
        return data.decode("latin-1").strip("\x00").strip()

    async def _run(self, frames: list[bytes], write: bool = False) -> list[str]:
        """Send frames in order, one reply each, on one connection.

        Not every AMS firmware is known to answer several commands per connection. If the amp goes silent
        (or hangs up) after a frame, that frame is retried once on a fresh connection, so the worst case is
        one short connection per frame, which is exactly how the amp was verified.
        """
        async with self._lock:
            reader, writer = await self._open()
            try:
                replies: list[str] = []
                for frame in frames:
                    reply = ""
                    try:
                        reply = await self._exchange(reader, writer, frame)
                    except TriadError:
                        pass
                    if not reply:
                        self._close(writer)
                        reader, writer = await self._open()
                        reply = await self._exchange(reader, writer, frame)
                    replies.append(reply)
                    if write:
                        await asyncio.sleep(WRITE_SETTLE)
                return replies
            except (OSError, ConnectionError) as err:
                raise TriadError(f"Communication error: {err}") from err
            finally:
                self._close(writer)

    @staticmethod
    def _close(writer: asyncio.StreamWriter) -> None:
        try:
            writer.close()
        except (OSError, RuntimeError):
            pass

    async def read_outputs(self, outputs: list[int]) -> dict[int, OutputState]:
        """Read source, volume and mute of the given 1-based outputs in one session."""
        frames: list[bytes] = []
        owners: list[int] = []
        for out in outputs:
            for cmd in (protocol.CMD_SOURCE, protocol.CMD_VOLUME, protocol.CMD_MUTE):
                frames.append(protocol.get_frame(cmd, out))
                owners.append(out)
        replies = await self._run(frames)
        states = {out: OutputState() for out in outputs}
        for out, reply in zip(owners, replies):
            if reply and not protocol.parse_reply(reply, states[out]):
                _LOGGER.debug("Unparsed reply for Out[%d]: %r", out, reply)
        return states

    async def read_eq(self, outputs: list[int]) -> dict[int, dict[str, float | None]]:
        """Read bass, treble and balance (dB) of the given 1-based outputs in one session.

        A value the amp did not answer, or that could not be parsed, is None.
        """
        frames: list[bytes] = []
        owners: list[tuple[int, str]] = []
        for out in outputs:
            for kind in protocol.EQ_KINDS:
                frames.append(protocol.eq_get_frame(kind, out))
                owners.append((out, kind))
        replies = await self._run(frames)
        result: dict[int, dict[str, float | None]] = {out: {} for out in outputs}
        for (out, kind), reply in zip(owners, replies):
            value = protocol.parse_eq(kind, reply) if reply else None
            if reply and value is None:
                _LOGGER.debug("Unparsed %s reply for Out[%d]: %r", kind, out, reply)
            result[out][kind] = value
        return result

    async def write(self, frames: list[bytes]) -> list[str]:
        """Send set frames; raises TriadError if the amp answers 'Command error' or nothing."""
        replies = await self._run(frames, write=True)
        for frame, reply in zip(frames, replies):
            if not reply or protocol.is_error(reply):
                raise TriadError(f"Amp rejected {frame.hex(' ')}: {reply!r}")
        return replies
