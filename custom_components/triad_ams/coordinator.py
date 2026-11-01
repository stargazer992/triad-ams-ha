"""Polling coordinator + the single place that writes to the amp."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import protocol
from .client import TriadClient, TriadError
from .const import (
    CONF_DEFAULT_INPUT,
    CONF_INPUTS,
    CONF_MODE,
    CONF_OUTPUTS,
    CONF_POLL_SECONDS,
    CONF_SOURCE_PLAYERS,
    DEFAULT_INPUT,
    DEFAULT_INPUTS,
    DEFAULT_MODE,
    DEFAULT_ON_VOLUME,
    DEFAULT_OUTPUTS,
    DEFAULT_POLL_SECONDS,
    DOMAIN,
    EQ_POLL_SECONDS,
    EQ_RETRY_SECONDS,
    MIN_POLL_SECONDS,
    MODE_CONTROL,
    RECENT_ERRORS,
    SNAPSHOT_KEY,
    SNAPSHOT_STORAGE_VERSION,
    VOLUME_CURVE_KEY,
    VOLUME_SEEK_STEPS,
)
from .protocol import OutputState

_LOGGER = logging.getLogger(__name__)

type TriadConfigEntry = ConfigEntry["TriadCoordinator"]


def _int_keys(mapping: dict[str, str], maximum: int) -> dict[int, str]:
    result: dict[int, str] = {}
    for key, value in mapping.items():
        try:
            number = int(key)
        except (TypeError, ValueError):
            continue
        if 1 <= number <= maximum:
            result[number] = str(value)
    return result


class TriadCoordinator(DataUpdateCoordinator[dict[int, OutputState]]):
    """data = {output number: OutputState} for every configured output."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: TriadClient) -> None:
        options = entry.options
        poll = max(MIN_POLL_SECONDS, int(options.get(CONF_POLL_SECONDS, DEFAULT_POLL_SECONDS)))
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry, update_interval=timedelta(seconds=poll))
        self.client = client
        self.outputs = _int_keys(options.get(CONF_OUTPUTS, DEFAULT_OUTPUTS), protocol.MAX_OUTPUTS)
        self.inputs = _int_keys(options.get(CONF_INPUTS, DEFAULT_INPUTS), protocol.MAX_INPUTS)
        self.default_input = int(options.get(CONF_DEFAULT_INPUT, DEFAULT_INPUT))
        self.source_players: dict[int, str] = _int_keys(options.get(CONF_SOURCE_PLAYERS, {}), protocol.MAX_INPUTS)
        self.hub_device_id: str | None = None          # registry id of the amp device (rooms hang off it)
        self.entity_ids: dict[int, str] = {}           # output -> media_player entity id (filled by the entities)
        self.last_volume: dict[int, int] = {}          # output -> last volume above the floor, used by turn_on
        self.last_source: dict[int, int] = {}          # output -> last real source, used by turn_on
        self.eq: dict[int, dict[str, float | None]] = {}   # output -> {bass|treble|balance: dB}; read every EQ_POLL_SECONDS
        self.loudness: dict[int, bool | None] = {}       # output -> loudness on/off; read with the tone controls
        self._eq_next = 0.0                                 # monotonic time the next tone read is due
        self._store: Store = Store(hass, SNAPSHOT_STORAGE_VERSION, f"{SNAPSHOT_KEY}.{entry.entry_id}")
        self._curve_store: Store = Store(hass, 1, f"{VOLUME_CURVE_KEY}.{entry.entry_id}")
        self._curve_pending = False
        self.recent_errors: deque[dict[str, str]] = deque(maxlen=RECENT_ERRORS)   # for the diagnostics download
        self._pending_volume: dict[int, int] = {}          # output -> newest requested volume not yet sent
        self._volume_locks: dict[int, asyncio.Lock] = {}

    # ---------- volume curve (learned from the amp's own SET replies) ----------
    async def async_load_curve(self) -> None:
        saved = await self._curve_store.async_load() or {}
        for value, db in (saved.get("learned") or {}).items():
            try:
                self.client.curve.learn(int(value), float(db))
            except (TypeError, ValueError):
                continue
        self.client.curve.dirty = False

    def _curve_data(self) -> dict:
        return {"learned": {str(v): db for v, db in sorted(self.client.curve.learned.items())}}

    def _save_curve(self) -> None:
        if self.client.curve.dirty:
            self.client.curve.dirty = False
            self._curve_pending = True
            self._curve_store.async_delay_save(self._curve_data, 10)

    async def async_flush_curve(self) -> None:
        """Write any learned points now, rather than losing a pending delayed save on unload."""
        if self.client.curve.dirty or self._curve_pending:
            self.client.curve.dirty = self._curve_pending = False
            await self._curve_store.async_save(self._curve_data())

    def _note_error(self, where: str, err: Exception) -> None:
        self.recent_errors.append({"time": dt_util.utcnow().isoformat(), "where": where, "error": str(err)})

    async def async_saved_snapshot(self) -> dict[str, Any] | None:
        return await self._store.async_load()

    # ---------- mode ----------
    @property
    def mode(self) -> str:
        return self.config_entry.options.get(CONF_MODE, DEFAULT_MODE)

    @property
    def can_write(self) -> bool:
        return self.mode == MODE_CONTROL

    def _require_control(self) -> None:
        if not self.can_write:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="monitor_mode")

    # ---------- polling ----------
    async def _async_update_data(self) -> dict[int, OutputState]:
        try:
            fresh = await self.client.read_outputs(sorted(self.outputs))
        except TriadError as err:
            self._note_error("poll", err)
            raise UpdateFailed(str(err)) from err
        states = self._merge(fresh, {})
        await self._poll_eq()
        return states

    def _merge(self, fresh: dict[int, OutputState], data: dict[int, OutputState]) -> dict[int, OutputState]:
        """Fold fresh reads into data. A field the amp did not answer keeps its last known value; an output
        never fully read stays out of data (its entities show unavailable rather than a made up 'off')."""
        previous = self.data or {}
        for out, state in fresh.items():
            prev = previous.get(out)
            if prev is not None:
                if "source" not in state.answered:
                    state.source = prev.source
                if "volume" not in state.answered:
                    state.volume_db, state.volume_value = prev.volume_db, prev.volume_value
                if "mute" not in state.answered:
                    state.muted = prev.muted
                state.answered |= prev.answered
            if not state.complete:
                _LOGGER.debug("Out[%d] not fully answered yet (%s), keeping it unavailable", out, sorted(state.answered))
                continue
            data[out] = state
            self._remember(out, state)
        return data

    async def _poll_eq(self, force: bool = False) -> None:
        """Read tone settings when due. Never fails the main poll: on error keep the last known values."""
        now = time.monotonic()
        if not force and now < self._eq_next:
            return
        try:
            fresh = await self.client.read_eq(sorted(self.outputs))
            loud = await self.client.read_loudness(sorted(self.outputs))
        except TriadError as err:
            _LOGGER.debug("Tone read failed, keeping last values: %s", err)
            self._note_error("tone", err)
            self._eq_next = now + EQ_RETRY_SECONDS             # try again soon, not on every poll
            return
        for out, kinds in fresh.items():
            merged = dict(self.eq.get(out, {}))
            for kind, value in kinds.items():
                if value is not None:
                    merged[kind] = value
                else:
                    merged.setdefault(kind, None)
            self.eq[out] = merged
        for out, value in loud.items():
            if value is not None or out not in self.loudness:
                self.loudness[out] = value
        self._eq_next = now + EQ_POLL_SECONDS

    def _remember(self, out: int, state: OutputState) -> None:
        if state.source is not None:
            self.last_source[out] = state.source
        if state.volume_value:
            self.last_volume[out] = state.volume_value

    def _on_volume(self, out: int) -> int:
        return self.last_volume.get(out) or DEFAULT_ON_VOLUME

    def _needs_volume(self, out: int) -> bool:
        """True if the room sits at the volume floor (Control4's idea of off), so switching it on must raise it."""
        state = (self.data or {}).get(out)
        return state is None or not state.volume_value

    async def _refresh_outputs(self, outputs: list[int]) -> None:
        """Re-read a few outputs right after a write so the UI shows the amp's own answer."""
        try:
            fresh = await self.client.read_outputs(sorted(set(outputs)))
        except TriadError as err:
            _LOGGER.debug("Post-write refresh failed: %s", err)
            await self.async_request_refresh()
            return
        self.async_set_updated_data(self._merge(fresh, dict(self.data or {})))

    # ---------- writes (all go through _write) ----------
    async def _write(self, frames: list[bytes]) -> list[str]:
        self._require_control()
        try:
            return await self.client.write(frames)
        except TriadError as err:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="amp_error", translation_placeholders={"error": str(err)}) from err
        finally:
            self._save_curve()

    async def _send(self, frames: list[bytes], outputs: list[int]) -> None:
        await self._write(frames)
        await self._refresh_outputs(outputs)

    async def async_set_source(self, output: int, input_number: int | None) -> None:
        await self._send([protocol.source_frame(output, input_number)], [output])

    async def async_set_volume_value(self, output: int, value: int) -> None:
        """value 0..100. The amp also clears mute when the volume is set.

        Latest wins: while one volume write to a room is on the wire, newer requests (a dragged slider) only
        replace the pending value, so a drag costs at most two writes instead of one per slider position."""
        self._require_control()
        self._pending_volume[output] = value
        lock = self._volume_locks.setdefault(output, asyncio.Lock())
        async with lock:
            latest = self._pending_volume.pop(output, None)
            if latest is None:
                return                                  # a newer request already sent the room's final value
            await self._send([protocol.volume_frame(output, latest)], [output])

    async def async_set_mute(self, output: int, mute: bool) -> None:
        await self._send([protocol.mute_frame(output, mute)], [output])

    async def async_set_eq(self, output: int, kind: str, db: float) -> None:
        """Set bass / treble / balance (dB). The amp's read-back lags a set, so the new value is shown right away
        and confirmed by the next scheduled tone read."""
        if kind not in protocol.EQ_KINDS:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="unknown_tone", translation_placeholders={"kind": kind})
        self._require_control()
        value = protocol.snap_eq_db(db)
        await self._write([protocol.eq_set_frame(kind, output, value)])
        self.eq.setdefault(output, {})[kind] = value
        self.async_update_listeners()

    async def async_set_loudness(self, output: int, on: bool) -> None:
        self._require_control()
        await self._write([protocol.loudness_frame(output, on)])
        self.loudness[output] = on
        self.async_update_listeners()

    def _on_frames(self, out: int, input_number: int) -> list[bytes]:
        frames = [protocol.source_frame(out, input_number)]
        if self._needs_volume(out):
            frames.append(protocol.volume_frame(out, self._on_volume(out)))   # also clears mute
        else:
            frames.append(protocol.mute_frame(out, False))
        return frames

    async def _ensure_unmuted(self, outputs: list[int]) -> None:
        """After waking rooms, make sure none stayed muted. The amp can ignore an unmute that follows a source change."""
        stuck = [out for out in outputs if (self.data or {}).get(out) and self.data[out].muted]
        if not stuck:
            return
        _LOGGER.debug("Outputs %s still muted after waking, retrying unmute", stuck)
        await self._send([protocol.mute_frame(out, False) for out in stuck], stuck)
        still = [out for out in stuck if (self.data or {}).get(out) and self.data[out].muted]
        if still:
            _LOGGER.warning("Triad outputs %s stayed muted after two unmute attempts", still)

    async def async_turn_on(self, output: int, input_number: int | None = None) -> None:
        source = input_number or self.last_source.get(output) or self.default_input
        await self._send(self._on_frames(output, source), [output])
        await self._ensure_unmuted([output])

    async def async_turn_off(self, output: int) -> None:
        """Off = Audio Off. (Control4 'off' leaves the route and drops the volume to the floor; both read as off here.)"""
        await self._send([protocol.source_frame(output, None)], [output])

    async def async_route(self, outputs: list[int], input_number: int | None) -> None:
        """Send several outputs to one input (or all off) in one session."""
        frames: list[bytes] = []
        for out in outputs:
            if input_number is None:
                frames.append(protocol.source_frame(out, None))
            else:
                frames.extend(self._on_frames(out, input_number))
        await self._send(frames, outputs)
        if input_number is not None:
            await self._ensure_unmuted(outputs)

    async def async_party(self, outputs: list[int], input_number: int, volume_value: int | None = None) -> None:
        """Every listed output to one input, optionally all at one volume, in one session."""
        if volume_value is None:
            await self.async_route(outputs, input_number)
            return
        frames: list[bytes] = []
        for out in outputs:
            frames += [protocol.source_frame(out, input_number), protocol.volume_frame(out, volume_value)]   # volume also clears mute
        await self._send(frames, outputs)
        await self._ensure_unmuted(outputs)

    # ---------- snapshot / restore (the rollback safety net) ----------
    async def async_snapshot(self) -> dict[str, Any]:
        """Save source, volume, mute, tone and loudness of every configured output to disk. Read only."""
        outputs = sorted(self.outputs)
        try:
            states = await self.client.read_outputs(outputs)
            tones = await self.client.read_eq(outputs)
            loud = await self.client.read_loudness(outputs)
        except TriadError as err:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="amp_error", translation_placeholders={"error": str(err)}) from err
        missing = [out for out, state in states.items() if not state.complete]
        if missing:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="snapshot_incomplete", translation_placeholders={"outputs": str(missing)})
        snap = {
            str(out): {
                "source": state.source,
                "volume_value": state.volume_value,
                "volume_db": state.volume_db,
                "muted": state.muted,
                **{kind: tones.get(out, {}).get(kind) for kind in protocol.EQ_KINDS},
                "loudness": loud.get(out),
            }
            for out, state in states.items()
        }
        self._save_curve()
        await self._store.async_save(snap)
        return snap

    async def _seek_volume(self, out: int, db: float) -> None:
        """Set the value whose dB is closest to db. Each SET reply reports the exact dB, so a few steps
        along the curve find the value Control4 had even where the measured curve is only interpolated."""
        curve = self.client.curve
        value = curve.to_value(db)
        best: tuple[float, int] | None = None
        direction = 0
        for _ in range(VOLUME_SEEK_STEPS):
            await self._write([protocol.volume_frame(out, value)])
            got = curve.learned.get(value)
            if got is None:
                return
            error = got - db
            if best is None or abs(error) < best[0]:
                best = (abs(error), value)
            if abs(error) <= protocol.VOLUME_MATCH_DB:
                return
            step = 1 if error < 0 else -1
            if direction and step != direction:
                break                                   # crossed the target: best is the closest value
            direction = step
            if not 0 <= value + step <= protocol.VOLUME_MAX:
                break
            value += step
        if best is not None and best[1] != value:
            await self._write([protocol.volume_frame(out, best[1])])

    async def async_restore(self) -> int:
        """Put every saved output back to its saved volume, mute, source and tone. Returns outputs restored."""
        self._require_control()
        snap = await self._store.async_load()
        if not snap:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_snapshot")
        outputs = sorted(int(key) for key in snap)
        for out in outputs:                                   # volume first: setting a volume also clears mute
            saved = snap[str(out)]
            if saved.get("volume_db") is not None:
                await self._seek_volume(out, float(saved["volume_db"]))
            elif saved.get("volume_value") is not None:      # snapshot from v0.2.1 or older
                await self._write([protocol.volume_frame(out, int(saved["volume_value"]))])
        frames: list[bytes] = []
        for out in outputs:
            saved = snap[str(out)]
            if saved.get("muted"):
                frames.append(protocol.mute_frame(out, True))
            frames.append(protocol.source_frame(out, saved.get("source")))
            for kind in protocol.EQ_KINDS:
                if saved.get(kind) is not None:
                    frames.append(protocol.eq_set_frame(kind, out, float(saved[kind])))
                    self.eq.setdefault(out, {})[kind] = protocol.snap_eq_db(float(saved[kind]))
            if saved.get("loudness") is not None:
                frames.append(protocol.loudness_frame(out, bool(saved["loudness"])))
                self.loudness[out] = bool(saved["loudness"])
        await self._send(frames, outputs)
        return len(outputs)
