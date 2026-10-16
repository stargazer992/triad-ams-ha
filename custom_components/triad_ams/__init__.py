"""Triad AMS audio matrix: one media player per room, volume, mute, on/off, source and grouping.

Safety model (same idea as the Crestron integration): a new entry starts in MONITOR mode, which only
reads. Nothing is ever written to the amp until you switch to CONTROL mode in the options, and
switching back to MONITOR (or the restore_snapshot service) is the rollback to Control4.
"""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import device_registry as dr

from .client import TriadClient
from .const import (
    CONF_HOST,
    CONF_PORT,
    DEFAULT_PORT,
    DOMAIN,
    SERVICE_ALL_OFF,
    SERVICE_PARTY,
    SERVICE_RESTORE,
    SERVICE_SNAPSHOT,
)
from .coordinator import TriadConfigEntry, TriadCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.MEDIA_PLAYER, Platform.NUMBER]


async def async_setup_entry(hass: HomeAssistant, entry: TriadConfigEntry) -> bool:
    client = TriadClient(entry.data[CONF_HOST], entry.data.get(CONF_PORT, DEFAULT_PORT))
    coordinator = TriadCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    if not coordinator.last_update_success:
        raise ConfigEntryNotReady("Triad AMS did not answer")
    entry.runtime_data = coordinator
    hub = dr.async_get(hass).async_get_or_create(              # the amp itself; every room hangs off it
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Triad AMS",
        manufacturer="Triad",
        model="AMS audio matrix",
        configuration_url=f"http://{entry.data[CONF_HOST]}",
    )
    coordinator.hub_device_id = hub.id
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    _register_services(hass)
    return True


async def _options_updated(hass: HomeAssistant, entry: TriadConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: TriadConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and not hass.config_entries.async_loaded_entries(DOMAIN):
        for name in (SERVICE_SNAPSHOT, SERVICE_RESTORE, SERVICE_ALL_OFF, SERVICE_PARTY):
            hass.services.async_remove(DOMAIN, name)
    return unloaded


def _coordinators(hass: HomeAssistant) -> list[TriadCoordinator]:
    return [e.runtime_data for e in hass.config_entries.async_loaded_entries(DOMAIN)]


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_SNAPSHOT):
        return

    async def snapshot(call: ServiceCall) -> dict:
        result: dict = {}
        for coordinator in _coordinators(hass):
            result = await coordinator.async_snapshot()
        return {"outputs_saved": len(result)}

    async def restore(call: ServiceCall) -> dict:
        restored = 0
        for coordinator in _coordinators(hass):
            restored += await coordinator.async_restore()
        return {"outputs_restored": restored}

    async def all_off(call: ServiceCall) -> None:
        for coordinator in _coordinators(hass):
            await coordinator.async_route(sorted(coordinator.outputs), None)

    async def party(call: ServiceCall) -> None:
        input_number = call.data.get("input")
        volume = call.data.get("volume")
        for coordinator in _coordinators(hass):
            source = input_number or coordinator.default_input
            if source not in coordinator.inputs and input_number:
                raise ServiceValidationError(f"Input {input_number} is not in the configured source list")
            await coordinator.async_route(sorted(coordinator.outputs), source)
            if volume is not None:
                for out in sorted(coordinator.outputs):
                    await coordinator.async_set_volume_value(out, round(volume * 100))

    hass.services.async_register(DOMAIN, SERVICE_SNAPSHOT, snapshot, supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, SERVICE_RESTORE, restore, supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, SERVICE_ALL_OFF, all_off)
    hass.services.async_register(
        DOMAIN,
        SERVICE_PARTY,
        party,
        schema=vol.Schema(
            {
                vol.Optional("input"): vol.All(vol.Coerce(int), vol.Range(min=1, max=24)),
                vol.Optional("volume"): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
            }
        ),
    )
