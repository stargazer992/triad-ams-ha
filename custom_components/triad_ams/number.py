"""Per-room bass / treble / balance sliders (added in v0.2.0, ported from the Control4 fork's Triad sliders)."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import protocol
from .const import CONF_HOST, DOMAIN
from .coordinator import TriadConfigEntry, TriadCoordinator

# kind -> (display name, icon)
KINDS = {
    "bass": ("Bass", "mdi:speaker"),
    "treble": ("Treble", "mdi:music-clef-treble"),
    "balance": ("Balance", "mdi:scale-balance"),
}


async def async_setup_entry(hass: HomeAssistant, entry: TriadConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        TriadTone(coordinator, entry, out, kind)
        for out in sorted(coordinator.outputs)
        for kind in protocol.EQ_KINDS
    )


class TriadTone(CoordinatorEntity[TriadCoordinator], NumberEntity):
    """One tone control of one room. Sits on the same device as the room's media player."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = protocol.EQ_MIN_DB
    _attr_native_max_value = protocol.EQ_MAX_DB
    _attr_native_step = 1.0
    _attr_native_unit_of_measurement = "dB"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: TriadCoordinator, entry: TriadConfigEntry, output: int, kind: str) -> None:
        super().__init__(coordinator)
        self._output = output
        self._kind = kind
        self._attr_name, self._attr_icon = KINDS[kind]
        self._attr_unique_id = f"{entry.entry_id}_out{output}_{kind}"
        # identical to the room's media player, so both land on one device whichever platform loads first
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_out{output}")},
            name=coordinator.outputs[output],
            manufacturer="Triad",
            model="AMS audio matrix",
            via_device_id=coordinator.hub_device_id,
            configuration_url=f"http://{entry.data[CONF_HOST]}",
        )

    @property
    def native_value(self) -> float | None:
        return self.coordinator.eq.get(self._output, {}).get(self._kind)

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_eq(self._output, self._kind, value)
