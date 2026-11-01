# The development install (one real house)

This is how the integration is used in the house it was written for, so that in a year we still know what is what.
None of it is built into the integration: it is entered in the setup / options form. Sources for this page: the amp
itself (live probes 2026-10-14 to 2026-10-31) and the `home-assistant-config` repo
(`pyscript/modules/music_zones.py` is the single source of truth for zones there).

## The hardware chain
```
Spotify / WiiM app --> WiiM Ultra (media_player.wiim_ultra_hardware)
                         | Line Out (fixed level: the WiiM's volume cannot be set)
                         v
                  Triad AMS 24x24, input 5   (TCP 192.168.104.98:52000)
                         | outputs 1-10
                         v
                  room speakers
```
The Triad room volume is the **only** gain stage in the house. Too quiet or too loud is fixed in the Triad room
levels (home-assistant-config `pyscript/modules/music_levels.py`), never on the WiiM.

The garage is **not** on the Triad. It has a WiiM Amp that joins the WiiM Ultra as a multiroom follower
(`input_boolean.fdl_garage`, `music_garage_join.py`), is manual-only and is left out of bulk outdoor actions.

## Amp outputs -> rooms -> Home Assistant
Verified by nudging each room's volume (2026-10-14). Outputs 11-24 are unused (Audio Off, volume 0). Output 24 is
the idle output used for volume scans and protocol tests.

| Output | Room (amp wiring) | Media player | Zone key in home-assistant-config | Dashboard label | 07:00 start |
|---|---|---|---|---|---|
| 1 | Living Room | `media_player.living_room_triad` | `living_room` | Living Room | yes |
| 2 | Master Bedroom | `media_player.master_bedroom_triad` | `master` | Master Bedroom | yes |
| 3 | Master Laundry | `media_player.master_laundry_triad` | `laundry` | Laundry Room | yes |
| 4 | Her Bathroom | `media_player.her_bathroom_triad` | `master_bath` | Master Bathroom | yes |
| 5 | Kitchen | `media_player.kitchen_triad` | `kitchen` | Kitchen | yes |
| 6 | Dining | `media_player.dining_triad` | `dining_room` | Dining Room | yes |
| 7 | Family Room | `media_player.family_room_triad` | `family_room` | Family Room | yes |
| 8 | Patio | `media_player.patio_triad` | `patio` | Patio | no (outdoor is manual) |
| 9 | Pool | `media_player.pool_triad` | `pool` | Pool | no (outdoor is manual) |
| 10 | Bar | `media_player.bar_triad` | `basement` | Basement | yes |

Each room's tone and loudness entities sit on the same device as its media player (Bass, Treble, Balance numbers and
the Loudness switch). Check the exact ids under Settings > Devices & services > Triad AMS > the room's device.

Shortcuts in home-assistant-config (not zones): `outdoor` = patio + pool, `master_suite` = master + master_bath +
laundry.

## Inputs
Input 5 is the WiiM Ultra; it is what every room plays and the input to link under options > Now playing
(`5=media_player.wiim_ultra_hardware`). Other inputs were not mapped when this was written.

## How it is configured
- Mode: control (Home Assistant drives the amp; Control4 audio is no longer used).
- Rooms: the ten outputs above. Sources: at least input 5. The live options are the authority: Settings > Devices &
  services > Triad AMS > Configure > Rooms / Sources / Now playing, or the diagnostics download.
- How Control4 used to leave the amp: "off" rooms stayed routed to input 5 at the volume floor (-108.5 dB); playing
  rooms sat at -55.6 dB (value 10). Both read correctly in the integration.

## Who writes to the Triad in home-assistant-config
Everything goes through one owner so there is a single writer:

| File | Role |
|---|---|
| `pyscript/modules/music_zones.py` | Zone table: zone key, label, trim helper, Triad players; `START_ZONES`, `OUTDOOR_ZONES`, `MASTER_SUITE_ZONES`. Reads only. |
| `pyscript/scripts/media/music_volume_control.py` | The engine and **sole owner** of Triad on/off and volume writes (`pyscript.music_zone_set`). |
| `pyscript/modules/music_levels.py` | House base level and per-zone start levels: `effective_pct = clamp(master_pct + trim(zone), 0, 100)`, written as `volume_level`. |
| `pyscript/scripts/media/music_scheduler.py` | Start / stop of whole-house music; turns zones on and off through the engine. |
| `pyscript/scripts/media/music_zone_playback.py` | Stops the WiiM when the last zone goes off; starts playback when the first zone comes on. |
| `pyscript/scripts/media/music_auto_eq.py`, `music_eq_sync.py` | Bass / treble presets by genre, and keeping the WiiM EQ preset in step with the Triad tone. |
| `www/fleur-*-card*.js` | Dashboard cards that show the `*_triad` players. |

If a room is ever rewired or renamed, update the integration options **and** `music_zones.py` (and this page).
