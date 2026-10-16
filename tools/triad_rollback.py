#!/usr/bin/env python3
"""Standalone snapshot / restore of the amp state. Needs no Home Assistant, only Python 3.

    python3 triad_rollback.py snapshot 192.168.104.98 amp_snapshot.json      (read only)
    python3 triad_rollback.py restore  192.168.104.98 amp_snapshot.json      (writes the amp)

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
    except Exception:
        return ""
    finally:
        s.close()


def get(host, cmd, out):
    return tx(host, [0xFF, 0x55, 0x04, 0x03, cmd, 0xF5, out - 1])


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


def restore(host, path):
    data = json.load(open(path))
    for key, saved in data.items():
        out = int(key)
        if saved["volume_db"] is not None:
            tx(host, [0xFF, 0x55, 0x04, 0x03, 0x1E, out - 1, db_to_value(saved["volume_db"])])
        if saved["muted"]:
            tx(host, [0xFF, 0x55, 0x03, 0x03, 0x17, out - 1])
        src = 0x18 if saved["source"] is None else saved["source"] - 1
        tx(host, [0xFF, 0x55, 0x04, 0x03, 0x1D, out - 1, src])
        print("restored output", out)


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] not in ("snapshot", "restore"):
        sys.exit(__doc__)
    (snapshot if sys.argv[1] == "snapshot" else restore)(sys.argv[2], sys.argv[3])
