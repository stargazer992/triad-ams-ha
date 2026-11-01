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
It is not linear. These are 11 of the 101 measured points; the integration has the full measured table (`protocol.VOLUME_POINTS`, see the first live write test below) and also learns the amp's exact dB from every volume SET reply. Volume up / down moves about 3 dB along that table (at least one value), so a press sounds the same at any level.

## Output map of the development install (verified by nudging each room's volume, 2026-10-14)
This is one house's wiring, kept as a worked example; the integration has no built-in rooms and asks for yours at setup.
1 Living Room, 2 Master Bedroom, 3 Master Laundry, 4 Her Bathroom, 5 Kitchen, 6 Dining, 7 Family Room, 8 Patio, 9 Pool, 10 Bar.
Outputs 11-24 are Audio Off, volume 0 (unused or not yet mapped). Input 5 = WiiM Ultra.

## Tone controls (bass / treble / balance) - verified on the real unit 2026-10-14, used by v0.2.0
| Function | Get | Set | Reply |
|---|---|---|---|
| Bass | `FF 55 04 03 2F F5 <out>` | `FF 55 04 03 2F <out> <val>` | `Get Out[n] Bass : -3` / `Set Out[n] Bass to 3` |
| Treble | `... 30 ...` | `... 30 <out> <val>` | `Get Out[n] Treble : 0` |
| Balance | `... 31 ...` | `... 31 <out> <val>` | `Bal Center`, `Bal R 1` (left is parsed as `Bal L n`, wording not yet seen on the amp) |
`<val>` = 0x00..0x30, 0x18 = flat/centre, 0.5 dB per step (-12..+12 dB). A read straight after a set can return the old value for a fraction of a second, so the integration shows the written value and confirms it on the next scheduled read.

## Loudness - verified on the real unit 2026-10-31 (output 24), used by v0.5.0
| Function | Frame | Reply |
|---|---|---|
| Get | `FF 55 04 03 1A F5 <out>` | `Get Out[n] Loudness status : Off` / `... : On` |
| On | `FF 55 04 03 1A <out> 01` (or `FF 55 03 03 1A <out>`) | `Set Out[n] Loudness On` |
| Off | `FF 55 04 03 1B <out> 00` (or `FF 55 03 03 1B <out>`) | `Set Out[n] Loudness Off` |
Both the 6- and 7-byte set forms worked; the integration sends the 7-byte form like every other SET. Outputs 1-10 all read Off on 2026-10-31; output 24 was left Off as found.

## Other frames seen in the AMS-16 driver (https://github.com/tim-weiler/triad-audio-matrix), NOT yet tested here
Input gain `FF 55 04 02 04`; parametric EQ under prefix `FF 56`. On that driver volume is 0..0xA1 and Audio Off is source 16; on the 24x24 volume tops out at 100 and Audio Off is 24 (0x18).

## Behaviour notes
- The amp sends no unsolicited updates: the integration polls (default every 10 s). Changes made by Control4 or a wall keypad show up on the next poll.
- A second client may compete with the Control4 controller; the integration therefore uses short sessions and starts in read only monitor mode.

## How Control4 leaves the amp (probe, 2026-10-16 15:40)
Control4 does not use Audio Off. A room that is "off" stays routed to input 5 with the volume at the floor (-108.5 dB; one room also muted).
Playing rooms were at -55.6 dB. The integration therefore treats a room as ON only when it is routed AND above the volume floor, turns a room on by routing it and raising it to its last real volume (default 10, about -55 dB), and uses Audio Off for its own "off".
Polling check: 30 queries on ONE connection returned clean in 0.31 s; five back-to-back sessions were fine.

## First live write test (Bar, output 10, 2026-10-16 15:55)
Through Home Assistant on the real amp: volume set, mute, volume up (also unmutes), turn off (Audio Off), turn on (restores source and last volume) all worked, and the amp's own read-back matched each time. The Bar was restored to -52.0 dB, muted, input 5.
The full 0-100 table was measured on output 24 on 2026-10-31 (`tools/triad_volume_scan.py`) and is `protocol.VOLUME_POINTS`. The 11 points above match it exactly; between them the old linear interpolation was off by up to about 2 dB (value 13 = -50.5 dB, interpolation said -52.7; value 10 = -55.6 dB, the Control4 level seen on the rooms).

## Quirk found live (Bar, 2026-10-16 16:01): unmute right after a source change is ignored
Joining a room that was Audio Off + muted routed it but left it muted: the amp acknowledged `Set Out[10] Unmute` but did not apply it when it arrived straight after the source change. An unmute on its own works, and so does setting a volume (which clears mute).
v0.1.4 pauses 0.25 s after every SET frame and, after waking a room, re-reads it and retries the unmute once if it is still muted.
