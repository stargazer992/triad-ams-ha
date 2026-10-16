"""End to end inside Home Assistant against the simulated amp."""
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.triad_ams.const import (
    CONF_DEFAULT_INPUT, CONF_HOST, CONF_INPUTS, CONF_MODE, CONF_OUTPUTS, CONF_POLL_SECONDS, CONF_PORT,
    DOMAIN, MODE_CONTROL, MODE_MONITOR,
)

ROOMS = {"1": "Living Room", "5": "Kitchen", "7": "Family Room"}
SOURCES = {"5": "WiiM Ultra", "2": "Apple TV"}


async def _setup(hass, amp, mode=MODE_MONITOR):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "127.0.0.1", CONF_PORT: amp.port},
        options={CONF_MODE: mode, CONF_OUTPUTS: ROOMS, CONF_INPUTS: SOURCES, CONF_DEFAULT_INPUT: 5, CONF_POLL_SECONDS: 10},
        unique_id="127.0.0.1",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _eid(hass, entry, out):
    return er.async_get(hass).async_get_entity_id("media_player", DOMAIN, f"{entry.entry_id}_out{out}")


async def test_monitor_mode_reads_and_never_writes(hass, amp):
    amp.source[4] = 4                       # Kitchen on input 5, set "by Control4"
    amp.volume[4] = 48
    entry = await _setup(hass, amp)
    state = hass.states.get(_eid(hass, entry, 5))
    assert state.state == "on" and state.attributes["source"] == "WiiM Ultra"
    assert state.attributes["volume_level"] == pytest.approx(0.48)
    assert hass.states.get(_eid(hass, entry, 1)).state == "off"
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("media_player", "volume_set", {"entity_id": _eid(hass, entry, 5), "volume_level": 0.2}, blocking=True)
    assert all(f[4] in (0x1D, 0x1E, 0x17) and f[5] == 0xF5 for f in amp.frames)    # only GET frames were ever sent


async def test_control_mode_commands(hass, amp):
    entry = await _setup(hass, amp, MODE_CONTROL)
    kitchen = _eid(hass, entry, 5)
    await hass.services.async_call("media_player", "turn_on", {"entity_id": kitchen}, blocking=True)
    assert amp.source[4] == 4                                  # default input 5 -> value 4
    await hass.services.async_call("media_player", "volume_set", {"entity_id": kitchen, "volume_level": 0.64}, blocking=True)
    assert amp.volume[4] == 64
    await hass.services.async_call("media_player", "volume_mute", {"entity_id": kitchen, "is_volume_muted": True}, blocking=True)
    assert amp.muted[4] is True
    assert hass.states.get(kitchen).attributes["is_volume_muted"] is True
    await hass.services.async_call("media_player", "volume_up", {"entity_id": kitchen}, blocking=True)
    assert amp.volume[4] == 69 and amp.muted[4] is False       # volume set clears mute on the amp
    await hass.services.async_call("media_player", "select_source", {"entity_id": kitchen, "source": "Apple TV"}, blocking=True)
    assert amp.source[4] == 1
    await hass.services.async_call("media_player", "turn_off", {"entity_id": kitchen}, blocking=True)
    assert amp.source[4] == 0x18 and hass.states.get(kitchen).state == "off"
    await hass.services.async_call("media_player", "turn_on", {"entity_id": kitchen}, blocking=True)
    assert amp.source[4] == 1                                  # remembers the last source (Apple TV)


async def test_grouping(hass, amp):
    entry = await _setup(hass, amp, MODE_CONTROL)
    kitchen, living, family = _eid(hass, entry, 5), _eid(hass, entry, 1), _eid(hass, entry, 7)
    await hass.services.async_call("media_player", "turn_on", {"entity_id": kitchen}, blocking=True)
    await hass.services.async_call("media_player", "join", {"entity_id": kitchen, "group_members": [living, family]}, blocking=True)
    assert amp.source[0] == amp.source[4] == amp.source[6] == 4
    assert set(hass.states.get(kitchen).attributes["group_members"]) == {kitchen, living, family}
    await hass.services.async_call("media_player", "unjoin", {"entity_id": living}, blocking=True)
    assert amp.source[0] == 0x18
    assert set(hass.states.get(kitchen).attributes["group_members"]) == {kitchen, family}


async def test_party_all_off_snapshot_restore(hass, amp):
    entry = await _setup(hass, amp, MODE_CONTROL)
    amp.source[4], amp.volume[4], amp.muted[4] = 1, 40, True       # state "from Control4"
    await hass.services.async_call(DOMAIN, "snapshot", {}, blocking=True, return_response=True)
    await hass.services.async_call(DOMAIN, "party", {"input": 5, "volume": 0.3}, blocking=True)
    assert amp.source[0] == amp.source[4] == amp.source[6] == 4 and amp.volume[6] == 30
    await hass.services.async_call(DOMAIN, "all_off", {}, blocking=True)
    assert amp.source[0] == amp.source[4] == amp.source[6] == 0x18
    result = await hass.services.async_call(DOMAIN, "restore_snapshot", {}, blocking=True, return_response=True)
    assert result == {"outputs_restored": 3}
    assert (amp.source[4], amp.volume[4], amp.muted[4]) == (1, 40, True)
    assert amp.source[0] == 0x18                                  # the rooms that were off are off again


async def test_amp_down_makes_rooms_unavailable(hass, amp):
    entry = await _setup(hass, amp)
    await amp.stop()
    coordinator = entry.runtime_data
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(_eid(hass, entry, 5)).state == "unavailable"


async def test_config_flow_and_rollback_menu(hass, amp):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: amp.port})
    assert result["type"] == "create_entry" and result["options"][CONF_MODE] == MODE_MONITOR     # starts read only
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    await hass.async_block_till_done()
    # take control (snapshot first), then roll back
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "control"})
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"confirm": True})
    assert flow["data"][CONF_MODE] == MODE_CONTROL
    await hass.async_block_till_done()
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "monitor"})
    assert flow["data"][CONF_MODE] == MODE_MONITOR


async def test_config_flow_unreachable(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: 1})
    assert result["errors"] == {"base": "cannot_connect"}


def test_map_parsing():
    from custom_components.triad_ams.config_flow import parse_map
    assert parse_map("1=Living Room\n 5 = Kitchen \n") == {"1": "Living Room", "5": "Kitchen"}
    assert parse_map("25=Nope") is None and parse_map("x=Nope") is None and parse_map("") is None


async def test_control4_style_off_reads_as_off_and_turn_on_raises_volume(hass, amp):
    # What the real house looks like: Control4 'off' = still routed to input 5, volume at the floor.
    for n in (0, 4, 6):
        amp.source[n] = 4
    amp.volume[4] = 10                                  # Kitchen is playing (quiet)
    entry = await _setup(hass, amp, MODE_CONTROL)
    living, kitchen, family = _eid(hass, entry, 1), _eid(hass, entry, 5), _eid(hass, entry, 7)
    assert hass.states.get(living).state == "off"       # routed but at the floor
    assert hass.states.get(kitchen).state == "on"
    assert hass.states.get(kitchen).attributes.get("group_members") is None      # alone: the others are 'off'
    await hass.services.async_call("media_player", "turn_on", {"entity_id": living}, blocking=True)
    assert amp.volume[0] == 10 and amp.source[0] == 4 and hass.states.get(living).state == "on"
    await hass.services.async_call("media_player", "volume_set", {"entity_id": living, "volume_level": 0.5}, blocking=True)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": living}, blocking=True)
    assert amp.source[0] == 0x18
    await hass.services.async_call("media_player", "turn_on", {"entity_id": living}, blocking=True)
    assert amp.volume[0] == 50                          # remembered the last real volume
    # joining wakes rooms that sit at the floor
    await hass.services.async_call("media_player", "join", {"entity_id": kitchen, "group_members": [family]}, blocking=True)
    assert amp.volume[6] == 10 and hass.states.get(family).state == "on"
