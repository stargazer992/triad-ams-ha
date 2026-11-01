"""Per-room loudness switch (amp commands 0x1A on / 0x1B off, verified on the real unit 2026-10-31)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import TriadConfigEntry, TriadCoordinator
from .number import room_device_info


async def async_setup_entry(hass: HomeAssistant, entry: TriadConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    async_add_entities(TriadLoudness(coordinator, entry, out) for out in sorted(coordinator.outputs))


class TriadLoudness(CoordinatorEntity[TriadCoordinator], SwitchEntity):
    """Loudness of one room. Sits on the room's device next to its tone controls."""

    _attr_has_entity_name = True
    _attr_translation_key = "loudness"
    _attr_icon = "mdi:volume-vibrate"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: TriadCoordinator, entry: TriadConfigEntry, output: int) -> None:
        super().__init__(coordinator)
        self._output = output
        self._attr_unique_id = f"{entry.entry_id}_out{output}_loudness"
        self._attr_device_info = room_device_info(coordinator, entry, output)

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.loudness.get(self._output)

    @property
    def available(self) -> bool:
        return super().available and self.is_on is not None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_loudness(self._output, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_loudness(self._output, False)
