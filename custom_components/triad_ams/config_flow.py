"""Config flow (add the amp) and options flow (mode, rooms, sources, polling)."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .client import TriadClient, TriadError
from .const import (
    CONF_DEFAULT_INPUT,
    CONF_HOST,
    CONF_INPUTS,
    CONF_MODE,
    CONF_OUTPUTS,
    CONF_POLL_SECONDS,
    CONF_PORT,
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

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            try:
                await TriadClient(host, port).read_outputs([1])
            except TriadError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(host)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Triad AMS ({host})",
                    data={CONF_HOST: host, CONF_PORT: port},
                    options={
                        CONF_MODE: DEFAULT_MODE,
                        CONF_OUTPUTS: DEFAULT_OUTPUTS,
                        CONF_INPUTS: DEFAULT_INPUTS,
                        CONF_DEFAULT_INPUT: DEFAULT_INPUT,
                        CONF_POLL_SECONDS: DEFAULT_POLL_SECONDS,
                    },
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOST, default="192.168.104.98"): str, vol.Required(CONF_PORT, default=DEFAULT_PORT): int}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return TriadOptionsFlow()


class TriadOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["control", "monitor", "rooms", "sources", "settings"],
            description_placeholders={"mode": self.config_entry.options.get(CONF_MODE, DEFAULT_MODE)},
        )

    async def async_step_control(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Take over: snapshot the amp first so the rollback has something to restore."""
        if user_input is None:
            return self.async_show_form(step_id="control", data_schema=vol.Schema({vol.Required("confirm", default=False): bool}))
        if not user_input["confirm"]:
            return self.async_abort(reason="not_confirmed")
        coordinator = getattr(self.config_entry, "runtime_data", None)
        if coordinator is not None:
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
            data_schema=vol.Schema({vol.Required("rooms", default=format_map(options.get(CONF_OUTPUTS, DEFAULT_OUTPUTS))): str}),
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
                    vol.Required("sources", default=format_map(options.get(CONF_INPUTS, DEFAULT_INPUTS))): str,
                    vol.Required(CONF_DEFAULT_INPUT, default=options.get(CONF_DEFAULT_INPUT, DEFAULT_INPUT)): vol.All(int, vol.Range(min=1, max=24)),
                }
            ),
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
