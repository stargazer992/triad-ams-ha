#!/usr/bin/env python3
"""Measures the volume curve (value 0..100 -> dB) on ONE idle output and prints it for protocol.py.

Only use an output with nothing connected / Audio Off (output 24 on this house). It sets the volume
over the whole range, then returns the output to volume 0 and Audio Off.

    python3 triad_volume_scan.py 192.168.104.98 24
"""
import re
import socket
import sys
import time

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.104.98"
OUT = int(sys.argv[2]) if len(sys.argv) > 2 else 24


def tx(frame):
    s = socket.create_connection((HOST, 52000), timeout=3)
    s.sendall(bytes(frame))
    s.settimeout(1.5)
    try:
        return s.recv(200).decode("latin-1").strip("\x00").strip()
    except Exception:
        return ""
    finally:
        s.close()


points = []
for value in range(0, 101):
    reply = tx([0xFF, 0x55, 0x04, 0x03, 0x1E, OUT - 1, value])
    m = re.search(r"to (-?\d+(?:\.\d+)?)", reply)
    points.append((value, float(m.group(1)) if m else None))
    time.sleep(0.05)
tx([0xFF, 0x55, 0x04, 0x03, 0x1E, OUT - 1, 0])
tx([0xFF, 0x55, 0x04, 0x03, 0x1D, OUT - 1, 0x18])
print("VOLUME_TABLE =", points)
