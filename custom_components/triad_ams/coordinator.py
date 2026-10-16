"""Polling coordinator + the single place that writes to the amp."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import protocol
from .client import TriadClient, TriadError
from .const import (
    CONF_DEFAULT_INPUT,
    CONF_INPUTS,
    CONF_MODE,
    CONF_OUTPUTS,
    CONF_POLL_SECONDS,
    DEFAULT_INPUT,
    DEFAULT_INPUTS,
    DEFAULT_MODE,
    DEFAULT_ON_VOLUME,
    DEFAULT_OUTPUTS,
    DEFAULT_POLL_SECONDS,
    DOMAIN,
    MIN_POLL_SECONDS,
    MODE_CONTROL,
    SNAPSHOT_KEY,
    SNAPSHOT_STORAGE_VERSION,
)
from .protocol import OutputState

_LOGGER = logging.getLogger(__name__)

type TriadConfigEntry = ConfigEntry["TriadCoordinator"]


def _int_keys(mapping: dict[str, str]) -> dict[int, str]:
    result: dict[int, str] = {}
    for key, value in mapping.items():
        try:
            number = int(key)
        except (TypeError, ValueError):
            continue
        if 1 <= number <= protocol.MAX_OUTPUTS:
            result[number] = str(value)
    return result


class TriadCoordinator(DataUpdateCoordinator[dict[int, OutputState]]):
    """data = {output number: OutputState} for every configured output."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: TriadClient) -> None:
        options = entry.options
        poll = max(MIN_POLL_SECONDS, int(options.get(CONF_POLL_SECONDS, DEFAULT_POLL_SECONDS)))
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry, update_interval=timedelta(seconds=poll))
        self.client = client
        self.outputs = _int_keys(options.get(CONF_OUTPUTS, DEFAULT_OUTPUTS))
        self.inputs = _int_keys(options.get(CONF_INPUTS, DEFAULT_INPUTS))
        self.default_input = int(options.get(CONF_DEFAULT_INPUT, DEFAULT_INPUT))
        self.hub_device_id: str | None = None          # registry id of the amp device (rooms hang off it)
        self.entity_ids: dict[int, str] = {}           # output -> media_player entity id (filled by the entities)
        self.last_volume: dict[int, int] = {}          # output -> last volume above the floor, used by turn_on
        self.last_source: dict[int, int] = {}          # output -> last real source, used by turn_on
        self._store: Store = Store(hass, SNAPSHOT_STORAGE_VERSION, f"{SNAPSHOT_KEY}.{entry.entry_id}")

    # ---------- mode ----------
    @property
    def mode(self) -> str:
        return self.config_entry.options.get(CONF_MODE, DEFAULT_MODE)

    @property
    def can_write(self) -> bool:
        return self.mode == MODE_CONTROL

    def _require_control(self) -> None:
        if not self.can_write:
            raise HomeAssistantError(
                "Triad AMS is in monitor mode (read only), Control4 owns the amp. "
                "Switch to control mode in the integration options first."
            )

    # ---------- polling ----------
    async def _async_update_data(self) -> dict[int, OutputState]:
        try:
            states = await self.client.read_outputs(sorted(self.outputs))
        except TriadError as err:
            raise UpdateFailed(str(err)) from err
        for out, state in states.items():
            self._remember(out, state)
        return states

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
        data = dict(self.data or {})
        data.update(fresh)
        for out, state in fresh.items():
            self._remember(out, state)
        self.async_set_updated_data(data)

    # ---------- writes (all go through _send) ----------
    async def _send(self, frames: list[bytes], outputs: list[int]) -> None:
        self._require_control()
        try:
            await self.client.write(frames)
        except TriadError as err:
            raise HomeAssistantError(str(err)) from err
        await self._refresh_outputs(outputs)

    async def async_set_source(self, output: int, input_number: int | None) -> None:
        await self._send([protocol.source_frame(output, input_number)], [output])

    async def async_set_volume_value(self, output: int, value: int) -> None:
        """value 0..100. The amp also clears mute when the volume is set."""
        await self._send([protocol.volume_frame(output, value)], [output])

    async def async_set_mute(self, output: int, mute: bool) -> None:
        await self._send([protocol.mute_frame(output, mute)], [output])

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

    # ---------- snapshot / restore (the rollback safety net) ----------
    async def async_snapshot(self) -> dict[str, Any]:
        """Save the state of every configured output to disk. Read only."""
        try:
            states = await self.client.read_outputs(sorted(self.outputs))
        except TriadError as err:
            raise HomeAssistantError(str(err)) from err
        snap = {
            str(out): {
                "source": state.source,
                "volume_value": state.volume_value,
                "muted": state.muted,
            }
            for out, state in states.items()
        }
        await self._store.async_save(snap)
        return snap

    async def async_restore(self) -> int:
        """Put every saved output back to its saved source, volume and mute. Returns outputs restored."""
        self._require_control()
        snap = await self._store.async_load()
        if not snap:
            raise HomeAssistantError("No snapshot saved yet, run triad_ams.snapshot first.")
        frames: list[bytes] = []
        outputs: list[int] = []
        for key, saved in snap.items():
            out = int(key)
            outputs.append(out)
            if saved.get("volume_value") is not None:
                frames.append(protocol.volume_frame(out, int(saved["volume_value"])))
            if saved.get("muted"):
                frames.append(protocol.mute_frame(out, True))
            frames.append(protocol.source_frame(out, saved.get("source")))
        await self._send(frames, outputs)
        return len(outputs)
