"""One media player per configured amp output (room)."""
from __future__ import annotations

from homeassistant.components.media_player import MediaPlayerEntity, MediaPlayerEntityFeature, MediaPlayerState
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import protocol
from .const import CONF_HOST, DOMAIN
from .coordinator import TriadConfigEntry, TriadCoordinator
from .protocol import OutputState

VOLUME_STEP_DB = 3.0       # per volume up/down press: the same loudness change at any level (the amp's own "volume down" is 3 dB too)

FEATURES = (
    MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.SELECT_SOURCE
    | MediaPlayerEntityFeature.GROUPING
)
# offered while the room's input has a linked Home Assistant player (options > Now playing); they control that player
TRANSPORT = (
    MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
)
# copied from the linked player while this room is on its input
LINKED_ATTRS = ("media_title", "media_artist", "media_album_name", "media_content_type", "media_duration", "media_position", "media_position_updated_at")


async def async_setup_entry(hass: HomeAssistant, entry: TriadConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    async_add_entities([TriadRoom(coordinator, entry, out) for out in sorted(coordinator.outputs)])


class TriadRoom(CoordinatorEntity[TriadCoordinator], MediaPlayerEntity):
    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, coordinator: TriadCoordinator, entry: TriadConfigEntry, output: int) -> None:
        super().__init__(coordinator)
        self._output = output
        self._attr_unique_id = f"{entry.entry_id}_out{output}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_out{output}")},
            name=coordinator.outputs[output],
            manufacturer="Triad",
            model="AMS audio matrix",
            via_device_id=coordinator.hub_device_id,
            configuration_url=f"http://{entry.data[CONF_HOST]}",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.coordinator.entity_ids[self._output] = self.entity_id
        players = sorted(set(self.coordinator.source_players.values()))
        if players:
            self.async_on_remove(async_track_state_change_event(self.hass, players, self._linked_changed))

    @callback
    def _linked_changed(self, event: Event[EventStateChangedData]) -> None:
        if event.data["entity_id"] == self._linked_id:
            self.async_write_ha_state()

    # ---------- now playing (from the player linked to the room's input) ----------
    @property
    def _linked_id(self) -> str | None:
        state = self._state
        if state is None or not self._is_on(state):
            return None
        return self.coordinator.source_players.get(state.source)

    @property
    def _linked(self) -> State | None:
        entity_id = self._linked_id
        return self.hass.states.get(entity_id) if entity_id else None

    def _linked_attr(self, name: str):
        linked = self._linked
        return linked.attributes.get(name) if linked else None

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        return FEATURES | TRANSPORT if self._linked_id else FEATURES

    @property
    def media_title(self) -> str | None:
        return self._linked_attr("media_title")

    @property
    def media_artist(self) -> str | None:
        return self._linked_attr("media_artist")

    @property
    def media_album_name(self) -> str | None:
        return self._linked_attr("media_album_name")

    @property
    def media_content_type(self) -> str | None:
        return self._linked_attr("media_content_type")

    @property
    def media_duration(self) -> int | None:
        return self._linked_attr("media_duration")

    @property
    def media_position(self) -> int | None:
        return self._linked_attr("media_position")

    @property
    def media_position_updated_at(self):
        return self._linked_attr("media_position_updated_at")

    @property
    def entity_picture(self) -> str | None:
        """The linked player's artwork URL (already served through Home Assistant's own image proxy)."""
        return self._linked_attr("entity_picture")

    async def _linked_call(self, service: str) -> None:
        entity_id = self._linked_id
        if entity_id is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_linked_player")
        await self.hass.services.async_call("media_player", service, {"entity_id": entity_id}, blocking=True, context=self._context)

    async def async_media_play(self) -> None:
        await self._linked_call("media_play")

    async def async_media_pause(self) -> None:
        await self._linked_call("media_pause")

    async def async_media_stop(self) -> None:
        await self._linked_call("media_stop")

    async def async_media_next_track(self) -> None:
        await self._linked_call("media_next_track")

    async def async_media_previous_track(self) -> None:
        await self._linked_call("media_previous_track")

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
        return MediaPlayerState.ON if self._is_on(state) else MediaPlayerState.OFF

    @staticmethod
    def _is_on(state: OutputState) -> bool:
        """On = routed to an input AND above the volume floor. Control4 'off' keeps the route at the floor."""
        return state.source is not None and bool(state.volume_value)

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
        if state is None or not self._is_on(state):
            return None
        return self._input_name(state.source)

    @property
    def group_members(self) -> list[str] | None:
        """Every room currently on the same input (this room first). None when it plays alone."""
        state = self._state
        if state is None or not self._is_on(state):
            return None
        members = [self.entity_id]
        for out, other in sorted((self.coordinator.data or {}).items()):
            if out != self._output and self._is_on(other) and other.source == state.source:
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
        if self._linked_id:
            attrs["linked_player"] = self._linked_id
        return attrs

    # ---------- commands ----------
    async def async_turn_on(self) -> None:
        await self.coordinator.async_turn_on(self._output)

    async def async_turn_off(self) -> None:
        await self.coordinator.async_turn_off(self._output)

    async def async_set_volume_level(self, volume: float) -> None:
        await self.coordinator.async_set_volume_value(self._output, round(volume * 100))

    async def _step(self, direction: int) -> None:
        """Move about VOLUME_STEP_DB along the amp's curve, and always at least one value."""
        state = self._state
        current = state.volume_value if state and state.volume_value is not None else 0
        curve = self.coordinator.client.curve
        target = curve.to_value(curve.to_db(current) + direction * VOLUME_STEP_DB)
        if direction > 0:
            target = max(target, current + 1)
        else:
            target = min(target, current - 1)
        await self.coordinator.async_set_volume_value(self._output, max(0, min(protocol.VOLUME_MAX, target)))

    async def async_volume_up(self) -> None:
        await self._step(1)

    async def async_volume_down(self) -> None:
        await self._step(-1)

    async def async_mute_volume(self, mute: bool) -> None:
        await self.coordinator.async_set_mute(self._output, mute)

    async def async_select_source(self, source: str) -> None:
        for number in self.coordinator.inputs:
            if self._input_name(number) == source:
                await self.coordinator.async_turn_on(self._output, number)
                return
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unknown_source", translation_placeholders={"source": source})

    async def async_join_players(self, group_members: list[str]) -> None:
        """Put the listed rooms on the same input as this room (or the default input if this room is off)."""
        state = self._state
        source = state.source if state and self._is_on(state) else self.coordinator.default_input
        wanted: list[int] = []
        by_entity = {entity_id: out for out, entity_id in self.coordinator.entity_ids.items()}
        for entity_id in group_members:
            out = by_entity.get(entity_id)
            if out is None:
                raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_a_room", translation_placeholders={"entity_id": entity_id})
            wanted.append(out)
        if self._output not in wanted:
            wanted.append(self._output)
        await self.coordinator.async_route(wanted, source)

    async def async_unjoin_player(self) -> None:
        await self.coordinator.async_turn_off(self._output)
