"""One media player per configured amp output (room)."""
from __future__ import annotations

from homeassistant.components.media_player import MediaPlayerEntity, MediaPlayerEntityFeature, MediaPlayerState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_HOST, DOMAIN
from .coordinator import TriadConfigEntry, TriadCoordinator
from .protocol import OutputState

VOLUME_STEP = 5            # amp units (0..100) per volume up/down press

FEATURES = (
    MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.SELECT_SOURCE
    | MediaPlayerEntityFeature.GROUPING
)


async def async_setup_entry(hass: HomeAssistant, entry: TriadConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    async_add_entities([TriadRoom(coordinator, entry, out) for out in sorted(coordinator.outputs)])


class TriadRoom(CoordinatorEntity[TriadCoordinator], MediaPlayerEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = FEATURES

    def __init__(self, coordinator: TriadCoordinator, entry: TriadConfigEntry, output: int) -> None:
        super().__init__(coordinator)
        self._output = output
        self._attr_unique_id = f"{entry.entry_id}_out{output}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_out{output}")},
            name=coordinator.outputs[output],
            manufacturer="Triad",
            model="AMS audio matrix",
            via_device=(DOMAIN, entry.entry_id),
            configuration_url=f"http://{entry.data[CONF_HOST]}",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.coordinator.entity_ids[self._output] = self.entity_id

    # ---------- state ----------
    @property
    def _state(self) -> OutputState | None:
        return (self.coordinator.data or {}).get(self._output)

    @property
    def available(self) -> bool:
        return super().available and self._state is not None

    @property
    def state(self) -> MediaPlayerState | None:
        state = self._state
        if state is None:
            return None
        return MediaPlayerState.OFF if state.source is None else MediaPlayerState.ON

    @property
    def volume_level(self) -> float | None:
        state = self._state
        if state is None or state.volume_value is None:
            return None
        return state.volume_value / 100

    @property
    def is_volume_muted(self) -> bool | None:
        state = self._state
        return None if state is None else state.muted

    def _input_name(self, number: int) -> str:
        return self.coordinator.inputs.get(number, f"Input {number}")

    @property
    def source_list(self) -> list[str]:
        return [self._input_name(n) for n in sorted(self.coordinator.inputs)]

    @property
    def source(self) -> str | None:
        state = self._state
        if state is None or state.source is None:
            return None
        return self._input_name(state.source)

    @property
    def group_members(self) -> list[str] | None:
        """Every room currently on the same input (this room first). None when it plays alone."""
        state = self._state
        if state is None or state.source is None:
            return None
        members = [self.entity_id]
        for out, other in sorted((self.coordinator.data or {}).items()):
            if out != self._output and other.source == state.source:
                entity_id = self.coordinator.entity_ids.get(out)
                if entity_id:
                    members.append(entity_id)
        return members if len(members) > 1 else None

    @property
    def extra_state_attributes(self) -> dict:
        state = self._state
        attrs = {"amp_output": self._output, "mode": self.coordinator.mode}
        if state is not None:
            attrs["amp_input"] = state.source
            attrs["volume_db"] = state.volume_db
        return attrs

    # ---------- commands ----------
    async def async_turn_on(self) -> None:
        await self.coordinator.async_turn_on(self._output)

    async def async_turn_off(self) -> None:
        await self.coordinator.async_set_source(self._output, None)

    async def async_set_volume_level(self, volume: float) -> None:
        await self.coordinator.async_set_volume_value(self._output, round(volume * 100))

    async def _step(self, delta: int) -> None:
        state = self._state
        current = (state.volume_value if state and state.volume_value is not None else 0)
        await self.coordinator.async_set_volume_value(self._output, max(0, min(100, current + delta)))

    async def async_volume_up(self) -> None:
        await self._step(VOLUME_STEP)

    async def async_volume_down(self) -> None:
        await self._step(-VOLUME_STEP)

    async def async_mute_volume(self, mute: bool) -> None:
        await self.coordinator.async_set_mute(self._output, mute)

    async def async_select_source(self, source: str) -> None:
        for number in self.coordinator.inputs:
            if self._input_name(number) == source:
                await self.coordinator.async_turn_on(self._output, number)
                return
        raise ServiceValidationError(f"Unknown source {source!r}")

    async def async_join_players(self, group_members: list[str]) -> None:
        """Put the listed rooms on the same input as this room (or the default input if this room is off)."""
        state = self._state
        source = state.source if state and state.source else self.coordinator.default_input
        wanted: list[int] = []
        by_entity = {entity_id: out for out, entity_id in self.coordinator.entity_ids.items()}
        for entity_id in group_members:
            out = by_entity.get(entity_id)
            if out is None:
                raise ServiceValidationError(f"{entity_id} is not a Triad room")
            wanted.append(out)
        if self._output not in wanted:
            wanted.append(self._output)
        await self.coordinator.async_route(wanted, source)

    async def async_unjoin_player(self) -> None:
        await self.coordinator.async_set_source(self._output, None)
