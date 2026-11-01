<p align="center"><img src="custom_components/triad_ams/brand/logo.png" alt="Triad" width="320"></p>

# Triad AMS audio matrix for Home Assistant

A HACS custom integration for the Triad AMS audio matrix (tested on the 24x24) over TCP. Each room (amp output) becomes a
Home Assistant media player with on/off, volume, mute, source and grouping, plus bass, treble, balance and loudness. A
streamer (for example a WiiM) on an amp input feeds the matrix and Home Assistant routes it to rooms, so the house can
run without Control4. A new install is read only until you take control.

Current version: **v0.5.0**. What changed in each release: [CHANGELOG.md](CHANGELOG.md).

Other documents:

| Document | What it covers |
|---|---|
| [docs/installation.md](docs/installation.md) | The development install: amp outputs and inputs, room names, Home Assistant entities and which home-assistant-config files use them |
| [docs/rollback.md](docs/rollback.md) | Moving from Control4 to Home Assistant in stages, and every way back |
| [docs/protocol.md](docs/protocol.md) | The amp's TCP protocol as verified on the real unit, the measured volume curve and amp quirks |
| [CHANGELOG.md](CHANGELOG.md) | Release history |

## Contents
1. [Install](#install)
2. [Setup](#setup)
3. [Options (all settings)](#options-all-settings)
4. [Modes: monitor and control](#modes-monitor-and-control)
5. [Entities per room](#entities-per-room)
6. [Room behaviour (on, off, volume, grouping)](#room-behaviour)
7. [Now playing](#now-playing)
8. [Services](#services)
9. [Snapshot and rollback](#snapshot-and-rollback)
10. [Diagnostics and troubleshooting](#diagnostics-and-troubleshooting)
11. [What is stored where](#what-is-stored-where)
12. [Tools](#tools)
13. [How it talks to the amp](#how-it-talks-to-the-amp)
14. [Code map](#code-map)
15. [Development and releases](#development-and-releases)

## Install
HACS > Custom repositories > add `https://github.com/stargazer992/triad-ams-ha` (category Integration) > install >
restart Home Assistant > Settings > Devices & services > Add integration > **Triad AMS**.

The amp must be reachable from Home Assistant on TCP port 52000 (default).

## Setup
Adding the integration only reads the amp. Two steps:

1. **Amp address**: IP address or host name and port (default `52000`). Setup checks that it can connect.
2. **Rooms and sources**, one per line as `number=Name`, numbers 1-24:
   - **Rooms** are amp *outputs*: `5=Kitchen`. Only listed outputs get entities.
   - **Sources** are amp *inputs*: `5=Streamer`. These are the names in each room's source list.
   - **Default input**: the input a room is routed to when it is switched on and has no remembered source. Must be
     one of the listed sources.

The entry starts in **monitor** mode with a 10 s refresh. One entry per amp (the address is the unique id); add the
integration again for a second amp.

**Reconfigure** (Settings > Devices & services > Triad AMS > three dots > Reconfigure) changes only the address and
port, for example after the amp gets a new IP. Mode, rooms, sources and the snapshot are kept.

## Options (all settings)
Settings > Devices & services > Triad AMS > **Configure** opens a menu:

| Menu item | What it does | Stored as option |
|---|---|---|
| Take control | Saves a snapshot of every room, then switches to control mode. Refused if the amp can't be read. | `mode: control` |
| Roll back to monitor only | Stops all writes at once. Changes nothing on the amp. | `mode: monitor` |
| Rooms (outputs) | Edit the `output=Name` list. Removing a room deletes its device and entities. | `outputs` |
| Sources (inputs) | Edit the `input=Name` list and the default input. | `inputs`, `default_input` |
| Now playing | Optional `input=media_player.entity` lines; see [Now playing](#now-playing). Leave empty for none. | `source_players` |
| Settings | Refresh interval, 3-300 s (default 10). | `poll_seconds` |

Every change reloads the integration. Entity ids are made once, from the room name when the room is first added;
renaming a room later does not change ids already in use.

## Modes: monitor and control
- **Monitor** (default): reads only. Every write (on/off, volume, mute, source, join, tone, loudness, services other
  than `snapshot`) is refused with "monitor mode". Control4 or anything else can keep driving the amp.
- **Control**: Home Assistant writes to the amp. Switching to control always takes a snapshot first; if the amp
  can't be read, control mode is not enabled.
- The amp accepts commands from every controller. If Control4 audio and Home Assistant both send commands, they
  overwrite each other. Keep Control4 audio idle while Home Assistant is in control.
- Every room has a `mode` attribute so automations can check the mode.

## Entities per room
Each room is a device named after the room (under the amp device). Entity ids come from the room name
(`Kitchen` gives `media_player.kitchen`, `number.kitchen_bass` ...); in the development install they were renamed to
`*_triad`, see [docs/installation.md](docs/installation.md).

| Entity | Type | What it is |
|---|---|---|
| `media_player.<room>` | media player | On/off, volume, mute, source, join/unjoin; now playing when linked |
| `number.<room>_bass` | number (config) | Bass -12..+12 dB, 1 dB steps in the UI (the amp holds 0.5 dB) |
| `number.<room>_treble` | number (config) | Treble -12..+12 dB |
| `number.<room>_balance` | number (config) | Balance -12 (left) .. 0 (centre) .. +12 (right) |
| `switch.<room>_loudness` | switch (config) | The amp's loudness on/off for that output |

Media player attributes besides the standard ones:

| Attribute | Meaning |
|---|---|
| `amp_output` | The amp output number (1-24) |
| `amp_input` | The input it is routed to (1-24), or none for Audio Off |
| `volume_db` | The volume the amp reports, in dB |
| `mode` | `monitor` or `control` |
| `linked_player` | The Home Assistant player feeding its input (only when linked under Now playing) |

Bass, treble, balance and loudness are read at startup and every 5 minutes (they rarely change), and after a failed
read again after 60 s. A failed tone read never makes the room players unavailable. A slider or switch change shows at
once and is written in control mode only.

## Room behaviour
- **State**: a room is **on** when it is routed to an input **and** above volume 0. Control4 turns a room "off" by
  leaving it routed at the volume floor, and the integration shows that as off too. **Audio Off** (no input) is off.
  A room the amp doesn't answer for becomes **unavailable**, never off. A room that answered before keeps its last
  values when one reply is missing.
- **turn_on**: routes the room to its last source (or the default input) and restores its last volume above the
  floor (value 10 = -55.6 dB if it has none).
- **turn_off**: routes the room to Audio Off.
- **volume_set(level)**: writes `round(level * 100)` as the amp value (0-100). The amp's curve is not linear: 0.10
  is -55.6 dB, 0.23 about -41 dB, 1.00 is 0 dB (full table in `docs/protocol.md`). Setting a volume also clears mute.
- **Volume up / down**: about 3 dB per press along the measured curve, always at least one value.
- **Slider drags**: while one volume write to a room is on the wire, newer positions only replace the waiting one,
  so a drag costs at most two writes per room and always ends at the last position.
- **Mute** / **unmute** are separate amp commands. The amp ignores an unmute right after a source change, so the
  integration pauses 0.25 s after every write and retries the unmute once.
- **select_source**: routes the room to that input.
- **Grouping**: `media_player.join` routes the joined rooms to this room's input (or the default input if this room
  is off); `unjoin` sends a room to Audio Off.
  `group_members` always lists the rooms that are on and on the same input, even when Control4 routed them.
- `volume_level` is absent while a room is off, so guard reads in templates and scripts.
- Tip: after `turn_on`, wait about 0.3 s before `volume_set` to avoid a short blip at the previous volume.

## Now playing
Optional. Options > Now playing, one line per input:

```
5=media_player.wiim_ultra_hardware
```

Every room routed to input 5 then shows that player's title, artist, album, content type, duration, position and
artwork, and updates when it changes. Play, pause, stop, next and previous on the room are sent to that player (rooms
on unlinked inputs don't offer them). The room's own state stays on/off, so dashboards and automations that check
`on` keep working. Only `media_player.*` entities are accepted. Volume stays on the amp: room volume never changes the
linked player's volume.

## Services
All in the `triad_ams` domain. Each takes an optional `config_entry_id` (pick an amp); without it the service acts on
every Triad AMS entry.

| Service | Mode | What it does |
|---|---|---|
| `triad_ams.snapshot` | any (read only) | Saves source, volume (exact dB), mute, bass, treble, balance and loudness of every configured room |
| `triad_ams.restore_snapshot` | control | Puts every room back as saved; steps each volume until the amp reports the saved dB |
| `triad_ams.all_off` | control | Every configured room to Audio Off |
| `triad_ams.party` | control | Every configured room to one `input` (1-24), optional `volume` (0-1), in one session |

Standard media player services (`turn_on`, `volume_set`, `join`, `media_play` ...) work on the room entities.

## Snapshot and rollback
A snapshot is taken automatically when you take control, and whenever you call `triad_ams.snapshot`. Only the
latest one is kept per amp. Ways back, from lightest to strongest:

1. Options > **Roll back to monitor only**: Home Assistant stops writing. Nothing on the amp changes.
2. `triad_ams.restore_snapshot` (still in control mode), then roll back to monitor: the amp is as it was when the
   snapshot was taken, including tone and loudness.
3. `tools/triad_rollback.py restore` from any computer, without Home Assistant (save a file with
   `snapshot` first).

Details and the staged Control4 cutover: [docs/rollback.md](docs/rollback.md).

## Diagnostics and troubleshooting
Settings > Devices & services > Triad AMS > three dots > **Download diagnostics**: options, mode, each room's last
reading (or unavailable), the saved snapshot, the learned volume points and the last 20 poll errors. The amp address
is removed.

| Symptom | Likely cause |
|---|---|
| All rooms unavailable | Amp unreachable, or not answering (another client holding the port, network). Check `recent_errors` in diagnostics. |
| "Monitor mode" error on a write | The entry is in monitor mode. Options > Take control. |
| Take control refused | The amp couldn't be read for the safety snapshot. Fix connectivity first. |
| A room shows on at a very low volume | It's routed and above 0 (Control4's floor is value 0). This is correct; turn it off to route Audio Off. |
| Tone or loudness unavailable, room fine | The 5-minute tone read failed; it retries after 60 s. |

## What is stored where
| Data | Where |
|---|---|
| Address, port | Config entry data (`.storage/core.config_entries`) |
| Mode, rooms, sources, default input, refresh, now playing | Config entry options (same file) |
| Snapshot | `.storage/triad_ams.snapshot.<entry_id>` |
| Learned volume points (value -> exact dB from the amp's replies) | `.storage/triad_ams.volume_curve.<entry_id>` |
| Last source and last volume above the floor per room (for turn_on) | Memory only; rebuilt from the first poll after a restart |

## Tools
Plain Python 3 scripts in `tools/`, run from any computer on the amp's network. None needs Home Assistant.

| Script | Writes? | Use |
|---|---|---|
| `triad_probe.py <amp-ip>` | no | Reads every output the way the integration polls; checks the amp's behaviour |
| `triad_rollback.py snapshot <amp-ip> file.json` | no | Saves source, volume, mute, bass, treble, balance, loudness of all 24 outputs |
| `triad_rollback.py restore <amp-ip> file.json` | yes | Puts the amp back from that file (older files without tone/loudness leave those alone) |
| `triad_volume_scan.py <amp-ip> <output>` | yes, one output | Measures value -> dB on one **idle** output and returns it to Audio Off, volume 0 |

## How it talks to the amp
- TCP port 52000. Frames start `FF 55`; replies are null-terminated text such as `Get Out[5] Volume : -55.6`.
  Outputs and inputs are 1-based here and 0-based on the wire.
- The amp has no push, so the integration polls every room's source, volume and mute every refresh interval
  (default 10 s), and tone/loudness every 5 minutes. A full read of all 24 outputs took about 7 s on the real amp.
- Short sessions only, serialised by one lock: connect, send, read, close. A session that loses its connection
  (while sending or reading) is retried once on a fresh one. A reply that names a different output is ignored.
- After every write it waits 0.25 s and reads the room back.
- Every frame, reply and quirk is in [docs/protocol.md](docs/protocol.md).

## Code map
| File | Role |
|---|---|
| `custom_components/triad_ams/protocol.py` | Frames, reply parsers, measured volume table (`VOLUME_POINTS`) and `VolumeCurve` |
| `client.py` | TCP client: sessions, retries, reads, writes, volume learning |
| `coordinator.py` | Polling, monitor/control gate, writes, snapshot/restore, tone and loudness state |
| `media_player.py` | Room entities, 3 dB steps, now playing |
| `number.py` / `switch.py` | Bass/treble/balance, and loudness |
| `config_flow.py` | Setup, reconfigure and the options menu |
| `__init__.py` / `services.yaml` | Platform setup and the `triad_ams.*` services |
| `diagnostics.py` | Diagnostics download |
| `translations/en.json` | All UI text, entity names and error messages |
| `tests/fake_amp.py` | Simulated amp (non-linear curve, silent / garbled / hang-up modes) used by the tests |

## Development and releases
- Tests: `pip install -r requirements_test.txt` (Python 3.14), then `pytest`. CI runs the tests, hassfest and the HACS
  check on every push.
- Release: bump `version` in `manifest.json`, add a section to `CHANGELOG.md`, merge to `main`, tag `vX.Y.Z`, and
  publish a GitHub release for the tag (HACS installs from releases).
- Anything new that writes to the amp must be refused in monitor mode and be covered by the snapshot.
