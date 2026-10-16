<p align="center"><img src="custom_components/triad_ams/brand/logo.png" alt="Triad" width="320"></p>

# Triad AMS audio matrix for Home Assistant

A HACS custom integration for the Triad AMS (tested on the 24x24) over TCP. It makes one media player per room with
volume, mute, on/off, source selection and grouping, so the house can run without Control4. A WiiM (or any source on an amp input)
feeds the matrix; Home Assistant routes it to rooms.

**Status: v0.2.1 (tone sliders, first shipped as v0.2.0) adds per-room bass / treble / balance sliders (v0.1.4 runs live on a real Triad AMS 24x24 in control mode: whole-house music, scheduler and dashboards drive it). A new install still starts read only.**

- **Safe by default**: a new entry only reads (monitor mode). You switch to control mode in the options, which saves a snapshot first.
- **Rollback**: options > roll back to monitor; `triad_ams.restore_snapshot`; or `tools/triad_rollback.py` without Home Assistant. See `docs/rollback.md`.
- **Rooms and sources are configurable** (options). Default rooms are the 10 outputs verified on site; default source is input 5 (WiiM Ultra).
- **Grouping**: `media_player.join` / `unjoin` route rooms to the same input; `group_members` always shows the rooms on the same input, even when Control4 changed it.
- **Tone controls (v0.2.0)**: three `number` entities per room (Bass, Treble, Balance, -12 to +12 dB, whole-dB steps in the UI, the amp holds 0.5 dB) on the same device as the room's media player. Values are read at startup and every 5 minutes; a slider move shows at once and is sent in control mode only (monitor mode refuses it). A failing tone read never takes the room players down.
- **Services**: `snapshot`, `restore_snapshot`, `all_off`, `party` (all rooms to one input, optional volume).
- **On/off like Control4 sees it**: a room is on when it is routed AND above the volume floor (Control4 "off" = still routed, volume at the floor). Turning on restores the last real volume (default about -55 dB).
- **Entity semantics** (for scripts): `on` = routed AND above the volume floor; `turn_on` routes to the default input and restores the last volume; `turn_off` = Audio Off; `volume_set(level)` writes round(level*100) as the amp value, and the amp curve is steeper than linear (about 23% = -41 dB); `volume_level` is absent while a room is off, so guard reads. Setting a volume also clears mute.
- **Amp quirk handled**: the amp ignores an unmute that arrives right after a source change. v0.1.4 pauses 0.25 s after every SET frame and retries the unmute once.
- **Tip**: after `turn_on`, wait about 0.3 s before `volume_set` to avoid a brief blip at the previous volume.
- **Polling** every 10 s (the amp has no push). Short sessions only, serialised, with a fresh-connection fallback.

## Install
HACS > Custom repositories > add this repo (category Integration) > install > restart > Settings > Devices & services > Add > Triad AMS.

See `docs/protocol.md` for the verified wire protocol and `docs/rollback.md` for cutover and rollback.
