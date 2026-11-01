"""Protocol + client against the simulated amp (frames match the real unit)."""
import pytest

import custom_components.triad_ams.client as c
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
    assert [v for v, _ in protocol.VOLUME_POINTS] == list(range(101))     # the full table measured on the unit
    assert protocol.db_to_value(-50.5) == 13 and protocol.db_to_value(-55.6) == 10
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


async def test_rejected_and_silent_amp_raise(amp, monkeypatch):
    client = TriadClient("127.0.0.1", amp.port)
    with pytest.raises(TriadError):
        await client.write([protocol.set_frame(protocol.CMD_VOLUME, 1, 0x70)])   # > 100 = Command error
    amp.silent = True
    monkeypatch.setattr(c, "REPLY_TIMEOUT", 0.2)
    with pytest.raises(TriadError):
        await client.write([protocol.volume_frame(1, 10)])


async def test_silent_amp_stops_the_session_early(amp, monkeypatch):
    """A read must not wait out every frame of a silent amp: give up after the fresh-connection retry."""
    amp.silent = True
    monkeypatch.setattr(c, "REPLY_TIMEOUT", 0.2)
    client = TriadClient("127.0.0.1", amp.port)
    with pytest.raises(TriadError):
        await client.read_outputs(list(range(1, 11)))
    assert len(amp.frames) == 2                      # first frame + its one retry, not 30 frames x 2


async def test_send_error_retries_on_fresh_connection(amp, monkeypatch):
    """A reset while sending (not only while reading) takes the fresh-connection retry."""
    real = TriadClient._exchange
    calls = {"n": 0}

    async def flaky(reader, writer, frame):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ConnectionResetError("peer reset")
        return await real(reader, writer, frame)

    monkeypatch.setattr(TriadClient, "_exchange", staticmethod(flaky))
    client = TriadClient("127.0.0.1", amp.port)
    states = await client.read_outputs([1, 2])
    assert all(s.complete for s in states.values())
    assert amp.connections == 2


def test_replies_must_name_the_asked_output():
    state = protocol.OutputState()
    assert not protocol.parse_reply("Get Out[6] Volume : -13.1", state, 5)
    assert state.volume_db is None and not state.answered
    assert protocol.parse_reply("Get Out[5] Volume : -13.1", state, 5) and state.answered == {"volume"}
    assert protocol.parse_eq("bass", "Get Out[3] Bass : 2", 4) is None
    assert protocol.parse_eq("bass", "Get Out[4] Bass : 2", 4) == 2.0


def test_volume_curve_learns_exact_points():
    curve = protocol.VolumeCurve()                    # a unit whose curve differs from the measured table
    assert curve.to_value(-51.6) == 12
    curve.learn(13, -51.6)                            # what the amp said in 'Set Out[n] Output Volume to -51.6'
    assert curve.to_value(-51.6) == 13 and curve.dirty
    assert curve.to_db(13) == -51.6
    assert protocol.parse_set_volume("Set Out[10] Output Volume to -50.5") == (10, -50.5)


async def test_write_learns_the_curve_from_set_replies(amp):
    client = TriadClient("127.0.0.1", amp.port)
    await client.write([protocol.volume_frame(3, 13)])
    assert client.curve.learned == {13: -50.5}
    assert (await client.read_outputs([3]))[3].volume_value == 13


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


def test_eq_encoding():
    assert protocol.db_to_eq_byte(0) == 0x18 and protocol.db_to_eq_byte(1) == 0x1A
    assert protocol.db_to_eq_byte(-12) == 0 and protocol.db_to_eq_byte(12) == 0x30
    assert protocol.db_to_eq_byte(99) == 0x30 and protocol.db_to_eq_byte(-99) == 0       # clamped
    assert protocol.db_to_eq_byte(0.5) == 0x19 and protocol.snap_eq_db(0.3) == 0.5
    assert protocol.eq_byte_to_db(0x15) == -1.5
    assert protocol.eq_get_frame("bass", 10) == bytes.fromhex("ff5504032ff509")
    assert protocol.eq_set_frame("treble", 10, 3) == bytes.fromhex("ff55040330091e")
    assert protocol.eq_set_frame("balance", 1, 0) == bytes.fromhex("ff55040331 0018".replace(" ", ""))


def test_parse_eq_replies():
    assert protocol.parse_eq("bass", "Get Out[2] Bass : -3") == -3.0
    assert protocol.parse_eq("bass", "Get Out[2] Bass : 0.5") == 0.5
    assert protocol.parse_eq("bass", "Set Out[2] Bass to 3") == 3.0
    assert protocol.parse_eq("treble", "Get Out[2] Treble : 0") == 0.0
    assert protocol.parse_eq("balance", "Get Out[2] Balance : Bal Center") == 0.0
    assert protocol.parse_eq("balance", "Get Out[2] Balance : Bal R 1") == 1.0
    assert protocol.parse_eq("balance", "Get Out[2] Balance : Bal L 4") == -4.0
    assert protocol.parse_eq("balance", "Set Out[10] Balance to Bal R 1") == 1.0
    assert protocol.parse_eq("bass", "Command error") is None
    assert protocol.parse_eq("bass", "Get Out[2] Treble : 3") is None                      # wrong kind


async def test_eq_read_write_roundtrip(amp):
    client = TriadClient("127.0.0.1", amp.port)
    base = await client.read_eq([1, 10])
    assert base == {1: {"bass": 0.0, "treble": 0.0, "balance": 0.0}, 10: {"bass": 0.0, "treble": 0.0, "balance": 0.0}}
    await client.write([protocol.eq_set_frame("bass", 10, 3), protocol.eq_set_frame("treble", 10, -2),
                        protocol.eq_set_frame("balance", 10, -4)])
    res = await client.read_eq([10, 1])
    assert res[10] == {"bass": 3.0, "treble": -2.0, "balance": -4.0}
    assert res[1] == {"bass": 0.0, "treble": 0.0, "balance": 0.0}                           # other outputs untouched


def _rollback_tool(port):
    import importlib.util
    import pathlib
    path = pathlib.Path(__file__).resolve().parents[1] / "tools" / "triad_rollback.py"
    spec = importlib.util.spec_from_file_location("triad_rollback", path)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    tool.PORT, tool.OUTPUTS = port, range(1, 4)
    return tool


def test_rollback_tool_parses_tone():
    tool = _rollback_tool(0)
    assert tool.parse_tone("bass", "Get Out[2] Bass : -3") == -3.0
    assert tool.parse_tone("balance", "Get Out[2] Balance : Bal Center") == 0.0
    assert tool.parse_tone("balance", "Get Out[2] Balance : Bal L 4") == -4.0
    assert tool.parse_tone("balance", "Get Out[2] Balance : Bal R 1.5") == 1.5
    assert tool.parse_tone("bass", "Command error") is None


async def test_rollback_tool_restores_tone(amp, tmp_path):
    import asyncio
    tool = _rollback_tool(amp.port)
    amp.eq[(0x2F, 1)], amp.eq[(0x31, 1)] = 0x18 - 4, 0x18 + 3      # out 2: bass -2 dB, balance R 1.5
    amp.source[1], amp.volume[1] = 4, 13
    amp.loudness[1] = True
    path = str(tmp_path / "snap.json")
    await asyncio.to_thread(tool.snapshot, "127.0.0.1", path)
    amp.eq[(0x2F, 1)] = amp.eq[(0x31, 1)] = 0x18
    amp.source[1], amp.volume[1] = 0x18, 50
    amp.loudness[1] = False
    await asyncio.to_thread(tool.restore, "127.0.0.1", path)
    assert amp.eq[(0x2F, 1)] == 0x18 - 4 and amp.eq[(0x31, 1)] == 0x18 + 3
    assert amp.source[1] == 4 and amp.volume[1] == 13
    assert amp.loudness[1] is True


def test_loudness_frames_and_replies():
    assert protocol.loudness_get_frame(24) == bytes.fromhex("ff55040 31af517".replace(" ", ""))
    assert protocol.loudness_frame(24, True) == bytes.fromhex("ff5504031a1701")
    assert protocol.loudness_frame(24, False) == bytes.fromhex("ff5504031b1700")
    assert protocol.parse_loudness("Get Out[24] Loudness status : On", 24) is True       # replies seen on the real amp
    assert protocol.parse_loudness("Set Out[24] Loudness Off", 24) is False
    assert protocol.parse_loudness("Get Out[3] Loudness status : On", 24) is None
    assert protocol.parse_loudness("Command error") is None
    tool = _rollback_tool(0)
    assert tool.parse_loudness("Get Out[24] Loudness status : On") is True
    assert tool.parse_loudness("Command error") is None
