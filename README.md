# Triad AMS audio matrix for Home Assistant

A HACS custom integration for the Triad AMS (tested on the 24x24) over TCP. It makes one media player per room with
volume, mute, on/off, source selection and grouping, so the house can run without Control4. A WiiM (or any source on an amp input)
feeds the matrix; Home Assistant routes it to rooms.

**Status: v0.1.1, tested against a simulated amp and verified against the real unit's protocol. Not yet run inside the house, so it starts read only.**

- **Safe by default**: a new entry only reads (monitor mode). You switch to control mode in the options, which saves a snapshot first.
- **Rollback**: options > roll back to monitor; `triad_ams.restore_snapshot`; or `tools/triad_rollback.py` without Home Assistant. See `docs/rollback.md`.
- **Rooms and sources are configurable** (options). Default rooms are the 10 outputs verified on site; default source is input 5 (WiiM Ultra).
- **Grouping**: `media_player.join` / `unjoin` route rooms to the same input; `group_members` always shows the rooms on the same input, even when Control4 changed it.
- **Services**: `snapshot`, `restore_snapshot`, `all_off`, `party` (all rooms to one input, optional volume).
- **On/off like Control4 sees it**: a room is on when it is routed AND above the volume floor (Control4 "off" = still routed, volume at the floor). Turning on restores the last real volume (default about -55 dB).
- **Polling** every 10 s (the amp has no push). Short sessions only, serialised, with a fresh-connection fallback.

## Install
HACS > Custom repositories > add this repo (category Integration) > install > restart > Settings > Devices & services > Add > Triad AMS.

See `docs/protocol.md` for the verified wire protocol and `docs/rollback.md` for cutover and rollback.
