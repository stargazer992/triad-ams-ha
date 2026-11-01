# Changelog

Every release is a GitHub release (HACS installs from releases). The version lives in
`custom_components/triad_ams/manifest.json`; commits that change it start with `vX.Y.Z:`.

## v0.5.0 (2026-10-31)
- Loudness: one `switch` per room. Amp commands 0x1A (on, also the read) and 0x1B (off), verified on a real 24x24.
  Read with the tone controls, written in control mode only, saved and restored by the snapshot and by `tools/triad_rollback.py`.
- Now playing (optional): options > Now playing links an amp input to the Home Assistant player that feeds it.
  A room on that input mirrors the player's title, artist, album and artwork; play / pause / stop / next / previous
  on the room control that player. The room state stays on / off.
- Volume up / down moves about 3 dB along the measured curve (always at least one value) instead of 5 raw values.
- Dragging a volume slider sends at most two writes per room (the one in flight and the latest position).
- Entity names (Bass, Treble, Balance, Loudness) and all error messages come from `translations/en.json`.

## v0.4.0 (2026-10-31)
- Setup asks for the amp address, then your rooms (outputs) and sources (inputs). Nothing house-specific is built in.
- Reconfigure changes the amp address / port and keeps mode, rooms, sources and the snapshot.
- Diagnostics download (mode, room states, snapshot, learned volume points, last 20 errors; amp address redacted).
- `snapshot`, `restore_snapshot`, `all_off`, `party` take an optional `config_entry_id` (one amp); default all amps.
- The full 0-100 volume table measured on a real 24x24 (output 24) is built in (`protocol.VOLUME_POINTS`).
- `tools/triad_rollback.py` also saves and restores bass, treble and balance.
- CI: pytest, hassfest and HACS validation on every push.

## v0.3.0 (2026-10-31)
- A silent amp makes rooms unavailable (not off) and the poll counts as failed; a single unanswered question keeps
  the room's last known value.
- Exact volume: the client learns each value's exact dB from the amp's SET replies and stores it across restarts.
  `restore_snapshot` steps the volume until the amp reports the saved dB.
- A connection dropped while sending is retried on a fresh connection.
- Control mode is refused when no safety snapshot can be taken.
- Snapshots also save bass, treble and balance; removing a room from the options deletes its device;
  `party` sends every room in one session; tone reads retry after 60 s.

## v0.2.0 / v0.2.1 (2026-10-16)
- Per-room Bass, Treble and Balance `number` entities (-12..+12 dB). v0.2.1 is a tag fix with the same code.

## v0.1.0 - v0.1.4 (2026-10-16)
- v0.1.0: rooms, volume, mute, on/off, sources, grouping, snapshot / rollback, monitor and control modes.
- v0.1.1: a room at the volume floor counts as off (how Control4 leaves "off" rooms); turn on restores the last volume.
- v0.1.2: `via_device_id` for the room devices.
- v0.1.3: Triad icon and logo in `brand/`.
- v0.1.4: 0.25 s pause after every SET frame, and a verified unmute retry after a source change (amp quirk).
