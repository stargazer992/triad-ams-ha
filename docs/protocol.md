# Triad AMS 24x24 protocol (verified 2026-10-16 on the real unit)

Unit: Triad AMS 24x24 at 192.168.104.98, TCP port 52000, plain binary frames, plain text replies ending in a null byte.
Outputs are zero based on the wire (byte 0 = "Out[1]"); 24 and above return "Command error". Inputs 1..24.

| Function | Frame | Reply |
|---|---|---|
| Get source | `FF 55 04 03 1D F5 <out>` | `Get Out[n] Input Source : input 6` or `Audio Off` |
| Get volume | `FF 55 04 03 1E F5 <out>` | `Get Out[n] Volume : -13.1` (dB) |
| Get mute | `FF 55 04 03 17 F5 <out>` | `Get Out[n] Mute status : mute` / `Unmute` |
| Set source | `FF 55 04 03 1D <out> <input-1>` | `Set Out[n] Input Source to input 5` |
| Audio Off | `FF 55 04 03 1D <out> 18` | `Set Out[n] Input Source to Audio Off` |
| Set volume | `FF 55 04 03 1E <out> <0..100>` | `Set Out[n] Output Volume to -13.1`; **also clears mute** |
| Mute | `FF 55 04 03 17 <out> <any>` or `FF 55 03 03 17 <out>` | `Set Out[n] Mute` |
| Unmute | `FF 55 04 03 18 <out> <any>` or `FF 55 03 03 18 <out>` | `Set Out[n] Unmute` |
| Volume -3 dB | `FF 55 04 03 16 <out> <any>` | `Set Out[n] Volume -3db` |

Values above 100 for volume and above 0x18 for source return `Command error`.

## Volume curve (value -> dB)
0:-108.5  8:-61.0  16:-47.7  32:-34.0  48:-22.5  64:-13.1  72:-9.3  80:-6.0  88:-3.3  96:-1.1  100:0.
It is not linear. The integration interpolates between these points; `tools/triad_volume_scan.py` measures all 101 values.

## Output map (verified by nudging each room's volume, 2026-10-14)
1 Living Room, 2 Master Bedroom, 3 Master Laundry, 4 Her Bathroom, 5 Kitchen, 6 Dining, 7 Family Room, 8 Patio, 9 Pool, 10 Bar.
Outputs 11-24 are Audio Off, volume 0 (unused or not yet mapped). Input 5 = WiiM Ultra.

## Other frames seen in the AMS-16 driver (https://github.com/tim-weiler/triad-audio-matrix), NOT yet tested here
Loudness on `FF 55 03 03 1A <out>`, off `FF 55 03 03 1B <out>`; get loudness `FF 55 04 03 1A F5 <out>`;
input gain `FF 55 04 02 04`; parametric EQ under prefix `FF 56`. On that driver volume is 0..0xA1 and Audio Off is source 16; on the 24x24 volume tops out at 100 and Audio Off is 24 (0x18).

## Behaviour notes
- The amp sends no unsolicited updates: the integration polls (default every 10 s). Changes made by Control4 or a wall keypad show up on the next poll.
- A second client may compete with the Control4 controller; the integration therefore uses short sessions and starts in read only monitor mode.

## How Control4 leaves the amp (probe, 2026-10-16 15:40)
Control4 does not use Audio Off. A room that is "off" stays routed to input 5 with the volume at the floor (-108.5 dB; one room also muted).
Playing rooms were at -55.6 dB. The integration therefore treats a room as ON only when it is routed AND above the volume floor, turns a room on by routing it and raising it to its last real volume (default 10, about -55 dB), and uses Audio Off for its own "off".
Polling check: 30 queries on ONE connection returned clean in 0.31 s; five back-to-back sessions were fine.

## First live write test (Bar, output 10, 2026-10-16 15:55)
Through Home Assistant on the real amp: volume set, mute, volume up (also unmutes), turn off (Audio Off), turn on (restores source and last volume) all worked, and the amp's own read-back matched each time. The Bar was restored to -52.0 dB, muted, input 5.
The real curve is steeper than the linear interpolation between measured points: value 13 reads -50.5 dB, where interpolation says about -52.7. Run `tools/triad_volume_scan.py` and paste the full table into `protocol.VOLUME_POINTS`.
