# Cutover and rollback (audio)

The goal is to run the house without Control4. Until then both can coexist, because the integration is read only by default.

## Stages
1. **Monitor (default).** Install, add the amp. HA shows every room (source, volume, mute) but writes nothing. Control4 runs the audio.
2. **Take control.** Options > Take control. A snapshot of every room is saved first. HA now drives the amp. Keep Control4 audio idle meanwhile, two writers fight.
3. **Retire Control4.** Remove the Control4 media players when you trust it.

## Rollback (any time)
- Options > "Roll back to monitor only": HA stops writing at once, nothing else changes.
- Want the amp exactly as it was before takeover: run service `triad_ams.restore_snapshot` first (needs control mode), then switch to monitor.
- HA is down: `python3 tools/triad_rollback.py restore 192.168.104.98 amp_snapshot.json` (take the file with `snapshot` beforehand, it is read only).
- Control4 itself does not need any change: it keeps its own settings and re-asserts them on its next use.

## Before the first takeover
1. `python3 tools/triad_probe.py` (read only) to confirm the amp answers the polling pattern.
2. `python3 tools/triad_rollback.py snapshot 192.168.104.98 amp_snapshot.json` and keep the file.
