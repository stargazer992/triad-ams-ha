"""Config flow (add the amp) and options flow (mode, rooms, sources, polling)."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback, valid_entity_id
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig

from .client import TriadClient, TriadError
from .const import (
    CONF_DEFAULT_INPUT,
    CONF_HOST,
    CONF_INPUTS,
    CONF_MODE,
    CONF_OUTPUTS,
    CONF_POLL_SECONDS,
    CONF_PORT,
    CONF_SOURCE_PLAYERS,
    DEFAULT_INPUT,
    DEFAULT_INPUTS,
    DEFAULT_MODE,
    DEFAULT_OUTPUTS,
    DEFAULT_POLL_SECONDS,
    DEFAULT_PORT,
    DOMAIN,
    MIN_POLL_SECONDS,
    MODE_CONTROL,
    MODE_MONITOR,
)

TEXT_AREA = TextSelector(TextSelectorConfig(multiline=True))


def _address_schema(values: Mapping[str, Any]) -> vol.Schema:
    host = values.get(CONF_HOST)
    return vol.Schema(
        {
            vol.Required(CONF_HOST, **({"default": host} if host else {})): str,
            vol.Required(CONF_PORT, default=values.get(CONF_PORT, DEFAULT_PORT)): int,
        }
    )


async def _can_connect(host: str, port: int) -> bool:
    try:
        await TriadClient(host, port).read_outputs([1])
    except TriadError:
        return False
    return True


def format_map(mapping: dict[str, str]) -> str:
    return "\n".join(f"{k}={v}" for k, v in sorted(mapping.items(), key=lambda kv: int(kv[0])))


def parse_map(text: str) -> dict[str, str] | None:
    """'1=Living Room' per line -> {'1': 'Living Room'}. None if any line is malformed or out of 1..24."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        key, sep, name = line.partition("=")
        key, name = key.strip(), name.strip()
        if not sep or not name or not key.isdigit() or not 1 <= int(key) <= 24:
            return None
        result[str(int(key))] = name
    return result or None


class TriadConfigFlow(ConfigFlow, domain=DOMAIN):
    """Adding the amp never writes to it: the entry starts in monitor (read only) mode."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 1: where the amp is (read only check that it answers)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            if await _can_connect(host, port):
                await self.async_set_unique_id(host)
                self._abort_if_unique_id_configured()
                self._data = {CONF_HOST: host, CONF_PORT: port}
                return await self.async_step_layout()
            errors["base"] = "cannot_connect"
        return self.async_show_form(step_id="user", data_schema=_address_schema(user_input or {}), errors=errors)

    async def async_step_layout(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 2: this house's rooms (outputs) and sources (inputs). Editable later in the options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            rooms, sources = parse_map(user_input["rooms"]), parse_map(user_input["sources"])
            default_input = user_input[CONF_DEFAULT_INPUT]
            if rooms is None:
                errors["rooms"] = "invalid_map"
            elif sources is None:
                errors["sources"] = "invalid_map"
            elif str(default_input) not in sources:
                errors[CONF_DEFAULT_INPUT] = "default_not_in_sources"
            else:
                return self.async_create_entry(
                    title=f"Triad AMS ({self._data[CONF_HOST]})",
                    data=self._data,
                    options={
                        CONF_MODE: DEFAULT_MODE,
                        CONF_OUTPUTS: rooms,
                        CONF_INPUTS: sources,
                        CONF_DEFAULT_INPUT: default_input,
                        CONF_POLL_SECONDS: DEFAULT_POLL_SECONDS,
                    },
                )
        user_input = user_input or {}
        return self.async_show_form(
            step_id="layout",
            data_schema=vol.Schema(
                {
                    vol.Required("rooms", default=user_input.get("rooms", format_map(DEFAULT_OUTPUTS))): TEXT_AREA,
                    vol.Required("sources", default=user_input.get("sources", format_map(DEFAULT_INPUTS))): TEXT_AREA,
                    vol.Required(CONF_DEFAULT_INPUT, default=user_input.get(CONF_DEFAULT_INPUT, DEFAULT_INPUT)): vol.All(int, vol.Range(min=1, max=24)),
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change the amp's address or port (e.g. it got a new IP) without losing rooms, snapshot or mode."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            if host != entry.data[CONF_HOST] and any(e.unique_id == host for e in self._async_current_entries(include_ignore=False)):
                return self.async_abort(reason="already_configured")
            if await _can_connect(host, port):
                return self.async_update_reload_and_abort(
                    entry, unique_id=host, title=f"Triad AMS ({host})", data_updates={CONF_HOST: host, CONF_PORT: port}
                )
            errors["base"] = "cannot_connect"
        return self.async_show_form(step_id="reconfigure", data_schema=_address_schema(user_input or entry.data), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return TriadOptionsFlow()


class TriadOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["control", "monitor", "rooms", "sources", "players", "settings"],
            description_placeholders={"mode": self.config_entry.options.get(CONF_MODE, DEFAULT_MODE)},
        )

    async def async_step_control(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Take over: snapshot the amp first so the rollback has something to restore."""
        if user_input is None:
            return self.async_show_form(step_id="control", data_schema=vol.Schema({vol.Required("confirm", default=False): bool}))
        if not user_input["confirm"]:
            return self.async_abort(reason="not_confirmed")
        coordinator = getattr(self.config_entry, "runtime_data", None)
        if coordinator is None:
            return self.async_abort(reason="not_loaded")             # no coordinator = no safety snapshot
        try:
            await coordinator.async_snapshot()
        except HomeAssistantError:
            return self.async_abort(reason="snapshot_failed")
        return self.async_create_entry(title="", data={**self.config_entry.options, CONF_MODE: MODE_CONTROL})

    async def async_step_monitor(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Rollback: stop writing. Control4 keeps working untouched; run triad_ams.restore_snapshot first if you want the saved state back."""
        return self.async_create_entry(title="", data={**self.config_entry.options, CONF_MODE: MODE_MONITOR})

    async def async_step_rooms(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        options = self.config_entry.options
        if user_input is not None:
            parsed = parse_map(user_input["rooms"])
            if parsed is None:
                errors["rooms"] = "invalid_map"
            else:
                return self.async_create_entry(title="", data={**options, CONF_OUTPUTS: parsed})
        return self.async_show_form(
            step_id="rooms",
            data_schema=vol.Schema({vol.Required("rooms", default=format_map(options.get(CONF_OUTPUTS, DEFAULT_OUTPUTS))): TEXT_AREA}),
            errors=errors,
        )

    async def async_step_sources(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        options = self.config_entry.options
        if user_input is not None:
            parsed = parse_map(user_input["sources"])
            default_input = user_input[CONF_DEFAULT_INPUT]
            if parsed is None:
                errors["sources"] = "invalid_map"
            elif str(default_input) not in parsed:
                errors[CONF_DEFAULT_INPUT] = "default_not_in_sources"
            else:
                return self.async_create_entry(title="", data={**options, CONF_INPUTS: parsed, CONF_DEFAULT_INPUT: default_input})
        return self.async_show_form(
            step_id="sources",
            data_schema=vol.Schema(
                {
                    vol.Required("sources", default=format_map(options.get(CONF_INPUTS, DEFAULT_INPUTS))): TEXT_AREA,
                    vol.Required(CONF_DEFAULT_INPUT, default=options.get(CONF_DEFAULT_INPUT, DEFAULT_INPUT)): vol.All(int, vol.Range(min=1, max=24)),
                }
            ),
            errors=errors,
        )

    async def async_step_players(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Which Home Assistant player feeds each amp input, so rooms on that input show its now playing."""
        errors: dict[str, str] = {}
        options = self.config_entry.options
        if user_input is not None:
            text = user_input.get("players", "")
            parsed = parse_map(text) if text.strip() else {}
            if parsed is None or not all(v.startswith("media_player.") and valid_entity_id(v) for v in parsed.values()):
                errors["players"] = "invalid_players"
            else:
                return self.async_create_entry(title="", data={**options, CONF_SOURCE_PLAYERS: parsed})
        return self.async_show_form(
            step_id="players",
            data_schema=vol.Schema({vol.Optional("players", default=format_map(options.get(CONF_SOURCE_PLAYERS, {}))): TEXT_AREA}),
            errors=errors,
        )

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        options = self.config_entry.options
        if user_input is not None:
            return self.async_create_entry(title="", data={**options, CONF_POLL_SECONDS: user_input[CONF_POLL_SECONDS]})
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {vol.Required(CONF_POLL_SECONDS, default=options.get(CONF_POLL_SECONDS, DEFAULT_POLL_SECONDS)): vol.All(int, vol.Range(min=MIN_POLL_SECONDS, max=300))}
            ),
        )
