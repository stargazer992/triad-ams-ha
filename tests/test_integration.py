"""End to end inside Home Assistant against the simulated amp."""
import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.triad_ams import protocol
from custom_components.triad_ams.const import (
    CONF_DEFAULT_INPUT,
    CONF_HOST,
    CONF_INPUTS,
    CONF_MODE,
    CONF_OUTPUTS,
    CONF_POLL_SECONDS,
    CONF_PORT,
    DOMAIN,
    MODE_CONTROL,
    MODE_MONITOR,
)
from tests.fake_amp import FakeAmp

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
    assert all(f[4] in (0x1D, 0x1E, 0x17, 0x2F, 0x30, 0x31) and f[5] == 0xF5 for f in amp.frames)    # only GET frames were ever sent


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
    await _setup(hass, amp, MODE_CONTROL)
    amp.source[4], amp.volume[4], amp.muted[4] = 1, 40, True       # state "from Control4"
    saved = await hass.services.async_call(DOMAIN, "snapshot", {}, blocking=True, return_response=True)
    assert saved == {"outputs_saved": 3}
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


async def test_party_volume_in_one_session(hass, amp):
    await _setup(hass, amp, MODE_CONTROL)
    before = amp.connections
    await hass.services.async_call(DOMAIN, "party", {"input": 5, "volume": 0.3}, blocking=True)
    assert [amp.volume[n] for n in (0, 4, 6)] == [30, 30, 30] and amp.source[0] == 4
    assert amp.connections - before == 2                         # one write session + one re-read, not one per room
    sets = [f for f in amp.frames if f[4] == protocol.CMD_VOLUME and f[5] != protocol.GET_FLAG]
    assert sets and all(f[6] == 30 for f in sets)                # never a blip at another volume


async def test_silent_amp_makes_rooms_unavailable_not_off(hass, amp, monkeypatch):
    import custom_components.triad_ams.client as c
    amp.source[4], amp.volume[4] = 4, 40
    entry = await _setup(hass, amp, MODE_CONTROL)
    assert hass.states.get(_eid(hass, entry, 5)).state == "on"
    amp.silent = True
    monkeypatch.setattr(c, "REPLY_TIMEOUT", 0.2)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert not entry.runtime_data.last_update_success
    assert hass.states.get(_eid(hass, entry, 5)).state == "unavailable"


async def test_unanswered_field_keeps_last_value(hass, amp):
    amp.source[4], amp.volume[4] = 4, 40
    entry = await _setup(hass, amp, MODE_CONTROL)
    amp.garble.add((0x1D, 4))                                    # Kitchen's source query now gets "Command error"
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get(_eid(hass, entry, 5))
    assert state.state == "on" and state.attributes["source"] == "WiiM Ultra"


async def test_room_never_answered_is_unavailable(hass, amp):
    amp.garble.add((0x1D, 6))                                    # Family Room source never answered
    entry = await _setup(hass, amp, MODE_CONTROL)
    assert hass.states.get(_eid(hass, entry, 7)).state == "unavailable"
    assert hass.states.get(_eid(hass, entry, 5)).state == "off"


async def test_volume_reads_back_as_written(hass, amp):
    # 13 sits between two measured points, where the built in curve reads the amp's -50.5 dB as 14.
    entry = await _setup(hass, amp, MODE_CONTROL)
    kitchen = _eid(hass, entry, 5)
    await hass.services.async_call("media_player", "turn_on", {"entity_id": kitchen}, blocking=True)
    await hass.services.async_call("media_player", "volume_set", {"entity_id": kitchen, "volume_level": 0.13}, blocking=True)
    assert hass.states.get(kitchen).attributes["volume_level"] == pytest.approx(0.13)
    amp.volume[4] = 0                                            # Control4 'off': volume to the floor
    await entry.runtime_data.async_refresh()
    await hass.services.async_call("media_player", "turn_on", {"entity_id": kitchen}, blocking=True)
    assert amp.volume[4] == 13                                   # no creep to 14
    await hass.services.async_call("media_player", "volume_up", {"entity_id": kitchen}, blocking=True)
    assert amp.volume[4] == 18


async def test_learned_curve_survives_restart(hass, amp):
    entry = await _setup(hass, amp, MODE_CONTROL)
    await hass.services.async_call("media_player", "volume_set", {"entity_id": _eid(hass, entry, 5), "volume_level": 0.13}, blocking=True)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    amp.volume[0] = 13
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.client.curve.learned.get(13) == -50.5
    assert entry.runtime_data.data[1].volume_value == 13


async def test_restore_lands_on_control4_volume_and_tone(hass, amp):
    amp.source[4], amp.volume[4] = 4, 13                         # set by Control4: HA never wrote 13
    amp.eq[(0x2F, 4)] = 0x18 + 6                                 # bass +3 dB
    await _setup(hass, amp, MODE_CONTROL)
    await hass.services.async_call(DOMAIN, "snapshot", {}, blocking=True, return_response=True)
    await hass.services.async_call(DOMAIN, "party", {"input": 2, "volume": 0.5}, blocking=True)
    amp.eq[(0x2F, 4)] = 0x18
    await hass.services.async_call(DOMAIN, "restore_snapshot", {}, blocking=True, return_response=True)
    assert (amp.source[4], amp.volume[4]) == (4, 13)
    assert amp.eq[(0x2F, 4)] == 0x18 + 6


async def test_removed_room_loses_its_device(hass, amp):
    from homeassistant.helpers import device_registry as dr
    entry = await _setup(hass, amp, MODE_CONTROL)
    devices = dr.async_get(hass)
    assert devices.async_get_device_by_identifier((DOMAIN, f"{entry.entry_id}_out7"), entry.entry_id)
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_OUTPUTS: {"1": "Living Room", "5": "Kitchen"}})
    await hass.async_block_till_done()
    assert devices.async_get_device_by_identifier((DOMAIN, f"{entry.entry_id}_out7"), entry.entry_id) is None
    assert _eid(hass, entry, 7) is None
    assert devices.async_get_device_by_identifier((DOMAIN, f"{entry.entry_id}_out5"), entry.entry_id)


async def test_take_control_refused_while_amp_unreachable(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "127.0.0.1", CONF_PORT: 1},
        options={CONF_MODE: MODE_MONITOR, CONF_OUTPUTS: ROOMS, CONF_INPUTS: SOURCES, CONF_DEFAULT_INPUT: 5, CONF_POLL_SECONDS: 10},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "control"})
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"confirm": True})
    assert flow["type"] == "abort" and flow["reason"] == "not_loaded"
    assert entry.options[CONF_MODE] == MODE_MONITOR


async def test_config_flow_and_rollback_menu(hass, amp):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: amp.port})
    assert result["type"] == "form" and result["step_id"] == "layout"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"rooms": "1=Den", "sources": "3=Radio", CONF_DEFAULT_INPUT: 3})
    assert result["type"] == "create_entry" and result["options"][CONF_MODE] == MODE_MONITOR     # starts read only
    assert result["options"][CONF_OUTPUTS] == {"1": "Den"} and result["options"][CONF_INPUTS] == {"3": "Radio"}
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


async def test_join_unmutes_even_if_amp_ignores_unmute_after_source_change(hass, amp):
    # Real amp behaviour seen on the Bar: an unmute right after a source change is acknowledged but ignored.
    amp.ignore_mute_after_source = 0.15
    amp.source[4], amp.volume[4] = 4, 10               # Kitchen playing
    amp.volume[0], amp.muted[0] = 13, True              # Living Room: off (Audio Off) and muted, as the Bar was
    entry = await _setup(hass, amp, MODE_CONTROL)
    kitchen, living = _eid(hass, entry, 5), _eid(hass, entry, 1)
    await hass.services.async_call("media_player", "join", {"entity_id": kitchen, "group_members": [living]}, blocking=True)
    assert amp.source[0] == 4
    assert amp.muted[0] is False                        # the retry fixed it
    assert hass.states.get(living).attributes["is_volume_muted"] is False


def _nid(hass, entry, out, kind):
    return er.async_get(hass).async_get_entity_id("number", DOMAIN, f"{entry.entry_id}_out{out}_{kind}")


async def test_tone_sliders_read_and_set(hass, amp):
    amp.eq[(0x2F, 4)] = 0x18 + 6                         # Kitchen bass already +3 dB on the amp
    entry = await _setup(hass, amp, MODE_CONTROL)
    bass = _nid(hass, entry, 5, "bass")
    assert hass.states.get(bass).state == "3.0"          # real value read at startup
    assert hass.states.get(_nid(hass, entry, 1, "balance")).state == "0.0"
    assert len([s for s in hass.states.async_all("number")]) == 9    # 3 rooms x 3 controls
    assert bass == "number.kitchen_bass"                              # named after the room, like its media player
    await hass.services.async_call("number", "set_value", {"entity_id": bass, "value": -4}, blocking=True)
    assert amp.eq[(0x2F, 4)] == 0x18 - 8                 # amp holds -4 dB
    assert hass.states.get(bass).state == "-4.0"         # shown straight away
    await hass.services.async_call("number", "set_value", {"entity_id": _nid(hass, entry, 7, "balance"), "value": 2}, blocking=True)
    assert amp.eq[(0x31, 6)] == 0x18 + 4 and amp.eq[(0x31, 4)] == 0x18      # only Family Room balance moved


async def test_tone_set_refused_in_monitor_mode(hass, amp):
    entry = await _setup(hass, amp, MODE_MONITOR)
    bass = _nid(hass, entry, 5, "bass")
    assert hass.states.get(bass).state == "0.0"
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("number", "set_value", {"entity_id": bass, "value": 5}, blocking=True)
    assert all(f[5] == 0xF5 for f in amp.frames)         # still only GET frames
    assert amp.eq[(0x2F, 4)] == 0x18


async def test_tone_read_failure_keeps_media_players(hass, amp):
    """A failing tone read must not take the room players down."""
    entry = await _setup(hass, amp, MODE_CONTROL)
    coordinator = entry.runtime_data
    from custom_components.triad_ams.client import TriadError

    async def boom(outputs):
        raise TriadError("tone read exploded")

    coordinator.client.read_eq = boom
    coordinator._eq_next = 0.0
    await coordinator.async_refresh()
    assert coordinator.last_update_success                                   # main poll unaffected
    assert hass.states.get(_eid(hass, entry, 5)).state != "unavailable"
    assert hass.states.get(_nid(hass, entry, 5, "bass")).state == "0.0"      # last known tone value kept


async def test_setup_layout_is_validated_and_has_no_house_defaults(hass, amp):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert "192.168" not in str(result["data_schema"].schema)          # no built-in amp address
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: amp.port})
    defaults = {str(k): k.default() for k in result["data_schema"].schema}
    assert "Kitchen" not in defaults["rooms"] and "WiiM" not in defaults["sources"]
    bad = await hass.config_entries.flow.async_configure(result["flow_id"], {"rooms": "nope", "sources": "1=A", CONF_DEFAULT_INPUT: 1})
    assert bad["errors"] == {"rooms": "invalid_map"}
    bad = await hass.config_entries.flow.async_configure(result["flow_id"], {"rooms": "1=A", "sources": "1=A", CONF_DEFAULT_INPUT: 2})
    assert bad["errors"] == {CONF_DEFAULT_INPUT: "default_not_in_sources"}


async def test_reconfigure_changes_address_and_keeps_options(hass, amp):
    entry = await _setup(hass, amp, MODE_CONTROL)
    other = FakeAmp()
    await other.start()
    try:
        flow = await entry.start_reconfigure_flow(hass)
        flow = await hass.config_entries.flow.async_configure(flow["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: 1})
        assert flow["errors"] == {"base": "cannot_connect"}
        flow = await hass.config_entries.flow.async_configure(flow["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: other.port})
        assert flow["type"] == "abort" and flow["reason"] == "reconfigure_successful"
        await hass.async_block_till_done()
        assert entry.data == {CONF_HOST: "127.0.0.1", CONF_PORT: other.port}
        assert entry.options[CONF_MODE] == MODE_CONTROL and entry.options[CONF_OUTPUTS] == ROOMS
        assert other.connections > 0
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await other.stop()


async def test_services_target_one_amp(hass, amp):
    second = FakeAmp()
    await second.start()
    try:
        first = await _setup(hass, amp, MODE_CONTROL)
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_HOST: "127.0.0.1", CONF_PORT: second.port},
            options={CONF_MODE: MODE_CONTROL, CONF_OUTPUTS: ROOMS, CONF_INPUTS: SOURCES, CONF_DEFAULT_INPUT: 5, CONF_POLL_SECONDS: 10},
            unique_id="second-amp",
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await hass.services.async_call(DOMAIN, "party", {"config_entry_id": entry.entry_id, "input": 2}, blocking=True)
        assert second.source[0] == 1 and amp.source[0] == 0x18            # only the named amp changed
        saved = await hass.services.async_call(DOMAIN, "snapshot", {"config_entry_id": first.entry_id}, blocking=True, return_response=True)
        assert saved == {"outputs_saved": len(ROOMS)}
        saved = await hass.services.async_call(DOMAIN, "snapshot", {}, blocking=True, return_response=True)
        assert saved == {"outputs_saved": 2 * len(ROOMS)}
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(DOMAIN, "all_off", {"config_entry_id": "nope"}, blocking=True)
        await hass.config_entries.async_unload(entry.entry_id)
    finally:
        await second.stop()


async def test_diagnostics(hass, amp):
    from custom_components.triad_ams.diagnostics import (
        async_get_config_entry_diagnostics,
    )
    amp.source[4], amp.volume[4] = 4, 13
    entry = await _setup(hass, amp, MODE_CONTROL)
    await hass.services.async_call(DOMAIN, "snapshot", {}, blocking=True)
    amp.silent = True
    await entry.runtime_data.async_refresh()
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["data"][CONF_HOST] == "**REDACTED**" and diag["mode"] == MODE_CONTROL
    assert diag["last_update_success"] is False and diag["recent_errors"][-1]["where"] == "poll"
    assert diag["outputs"]["5"]["name"] == "Kitchen" and diag["outputs"]["5"]["volume_value"] == 13
    assert diag["snapshot"]["5"]["source"] == 5
    amp.silent = False
