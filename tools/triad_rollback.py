#!/usr/bin/env python3
"""Standalone snapshot / restore of the amp state (source, volume, mute, bass, treble, balance). Needs no Home Assistant, only Python 3.

    python3 triad_rollback.py snapshot <amp-ip> amp_snapshot.json      (read only)
    python3 triad_rollback.py restore  <amp-ip> amp_snapshot.json      (writes the amp)

Use it before the first switch to control mode, and any time Home Assistant is down and the
amp must be put back as Control4 left it.
"""
import json
import re
import socket
import sys

PORT = 52000
OUTPUTS = range(1, 25)


def tx(host, frame):
    s = socket.create_connection((host, PORT), timeout=3)
    s.sendall(bytes(frame))
    s.settimeout(1.5)
    try:
        return s.recv(200).decode("latin-1").strip("\x00").strip()
    except OSError:
        return ""
    finally:
        s.close()


def get(host, cmd, out):
    return tx(host, [0xFF, 0x55, 0x04, 0x03, cmd, 0xF5, out - 1])


TONE = {"bass": 0x2F, "treble": 0x30, "balance": 0x31}     # value = 0x18 + 2*dB (balance: + right, - left)


def parse_tone(kind, reply):
    """'Get Out[2] Bass : -3' -> -3.0; balance 'Bal Center' -> 0, 'Bal R 1' -> 1, 'Bal L 4' -> -4. None if not understood."""
    if not re.search(rf"Out\[\d+\]\s+{kind}", reply, re.IGNORECASE):
        return None
    if kind == "balance":
        b = re.search(r"Bal(?:ance)?\s*(Center|Centre|[LR])?\s*(-?\d+(?:\.\d+)?)?", reply.split(":", 1)[-1], re.IGNORECASE)
        if not b:
            return None
        side, num = b.group(1), b.group(2)
        if side and side.lower().startswith("c"):
            return 0.0
        if num is None:
            return None
        return -abs(float(num)) if side and side.upper() == "L" else abs(float(num)) if side else float(num)
    n = re.search(r":\s*(-?\d+(?:\.\d+)?)", reply)
    return float(n.group(1)) if n else None


def set_tone(host, out, kind, db):
    value = max(0, min(0x30, round(0x18 + 2 * max(-12.0, min(12.0, db)))))
    tx(host, [0xFF, 0x55, 0x04, 0x03, TONE[kind], out - 1, value])


def snapshot(host, path):
    data = {}
    for out in OUTPUTS:
        src = get(host, 0x1D, out)
        vol = get(host, 0x1E, out)
        mute = get(host, 0x17, out)
        m = re.search(r"input (\d+)", src)
        v = re.search(r"Volume : (-?\d+(?:\.\d+)?)", vol)
        data[str(out)] = {
            "source": int(m.group(1)) if m else None,
            "volume_db": float(v.group(1)) if v else None,
            "muted": "mute" in mute.lower() and "unmute" not in mute.lower(),
            **{kind: parse_tone(kind, get(host, cmd, out)) for kind, cmd in TONE.items()},
        }
    json.dump(data, open(path, "w"), indent=2)
    print(f"saved {len(data)} outputs to {path}")


# measured curve (value -> dB); kept in step with custom_components/triad_ams/protocol.py
POINTS = [(0, -108.5), (8, -61.0), (16, -47.7), (32, -34.0), (48, -22.5), (64, -13.1), (72, -9.3), (80, -6.0), (88, -3.3), (96, -1.1), (100, 0.0)]


def db_to_value(db):
    if db <= POINTS[0][1]:
        return 0
    if db >= POINTS[-1][1]:
        return 100
    for (v0, d0), (v1, d1) in zip(POINTS, POINTS[1:]):
        if d0 <= db <= d1:
            return round(v0 + (v1 - v0) * (db - d0) / (d1 - d0))
    return 0


def set_volume(host, out, value):
    """Write a volume value; the amp's reply names the exact dB it landed on."""
    m = re.search(r"to (-?\d+(?:\.\d+)?)", tx(host, [0xFF, 0x55, 0x04, 0x03, 0x1E, out - 1, value]))
    return float(m.group(1)) if m else None


def seek_volume(host, out, target_db, steps=8):
    """The curve between measured points is only an estimate: step until the amp reports the saved dB."""
    value = db_to_value(target_db)
    db = set_volume(host, out, value)
    tried = {value}
    for _ in range(steps):
        if db is None or abs(db - target_db) <= 0.05:
            return
        value = max(0, min(100, value + (1 if db < target_db else -1)))
        if value in tried:
            return
        tried.add(value)
        db = set_volume(host, out, value)


def restore(host, path):
    data = json.load(open(path))
    for key, saved in data.items():
        out = int(key)
        if saved["volume_db"] is not None:
            seek_volume(host, out, saved["volume_db"])
        if saved["muted"]:
            tx(host, [0xFF, 0x55, 0x03, 0x03, 0x17, out - 1])
        src = 0x18 if saved["source"] is None else saved["source"] - 1
        tx(host, [0xFF, 0x55, 0x04, 0x03, 0x1D, out - 1, src])
        for kind in TONE:                                    # files from older versions have no tone: left as is
            if saved.get(kind) is not None:
                set_tone(host, out, kind, saved[kind])
        print("restored output", out)


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] not in ("snapshot", "restore"):
        sys.exit(__doc__)
    (snapshot if sys.argv[1] == "snapshot" else restore)(sys.argv[2], sys.argv[3])
