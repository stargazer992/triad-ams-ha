"""Protocol + client against the simulated amp (frames match the real unit)."""
import pytest

from custom_components.triad_ams import protocol
from custom_components.triad_ams.client import TriadClient, TriadError


def test_frames_match_verified_bytes():
    assert protocol.get_frame(protocol.CMD_VOLUME, 24) == bytes.fromhex("ff55040 31ef517".replace(" ", ""))
    assert protocol.volume_frame(24, 0x40) == bytes.fromhex("ff5504031e1740")
    assert protocol.source_frame(24, 5) == bytes.fromhex("ff5504031d1704")
    assert protocol.source_frame(24, None) == bytes.fromhex("ff5504031d1718")      # Audio Off = 0x18
    assert protocol.mute_frame(24, True) == bytes.fromhex("ff55040317 1701".replace(" ", ""))
    assert protocol.mute_frame(24, False) == bytes.fromhex("ff5504031817 00".replace(" ", ""))


def test_volume_curve_matches_measurements():
    for value, db in protocol.VOLUME_POINTS:
        assert protocol.value_to_db(value) == pytest.approx(db)
        assert protocol.db_to_value(db) == value
    assert protocol.db_to_value(-200) == 0
    assert protocol.db_to_value(5) == 100
    assert 0 < protocol.db_to_value(-69.0) < 8      # a real room volume seen on the unit


def test_parse_replies():
    state = protocol.OutputState()
    assert protocol.parse_reply("Get Out[5] Input Source : input 6", state) and state.source == 6
    assert protocol.parse_reply("Get Out[5] Input Source : Audio Off", state) and state.source is None
    assert protocol.parse_reply("Get Out[5] Volume : -69.0", state) and state.volume_db == -69.0
    assert protocol.parse_reply("Get Out[5] Mute status : Unmute", state) and state.muted is False
    assert protocol.parse_reply("Get Out[5] Mute status : mute", state) and state.muted is True
    assert not protocol.parse_reply("Command error", state)
    assert protocol.is_error("Command errorGet Out[24] Volume : -13.1")


async def test_read_write_roundtrip(amp):
    client = TriadClient("127.0.0.1", amp.port)
    states = await client.read_outputs([1, 24])
    assert states[1].source is None and states[1].muted is False
    await client.write([protocol.source_frame(24, 5), protocol.volume_frame(24, 64), protocol.mute_frame(24, True)])
    state = (await client.read_outputs([24]))[24]
    assert state.source == 5 and state.volume_db == pytest.approx(-13.1, abs=0.05) and state.muted is True
    await client.write([protocol.mute_frame(24, False)])
    assert (await client.read_outputs([24]))[24].muted is False
    assert amp.connections == 5          # one short session per call, never kept open


async def test_one_session_reads_many_outputs(amp):
    client = TriadClient("127.0.0.1", amp.port)
    before = amp.connections
    await client.read_outputs(list(range(1, 11)))
    assert amp.connections == before + 1


async def test_rejected_and_silent_amp_raise(amp):
    client = TriadClient("127.0.0.1", amp.port)
    with pytest.raises(TriadError):
        await client.write([protocol.set_frame(protocol.CMD_VOLUME, 1, 0x70)])   # > 100 = Command error
    amp.silent = True
    import custom_components.triad_ams.client as c
    c.REPLY_TIMEOUT = 0.2
    with pytest.raises(TriadError):
        await client.write([protocol.volume_frame(1, 10)])


async def test_unreachable_amp_raises():
    with pytest.raises(TriadError):
        await TriadClient("127.0.0.1", 1).read_outputs([1])


async def test_amp_that_hangs_up_after_every_reply(amp):
    amp.one_shot = True
    client = TriadClient("127.0.0.1", amp.port)
    states = await client.read_outputs([1, 2, 3])
    assert len(states) == 3 and all(s.complete for s in states.values())
    await client.write([protocol.source_frame(2, 5), protocol.volume_frame(2, 50)])
    assert amp.source[1] == 4 and amp.volume[1] == 50
