#!/usr/bin/env python3
"""READ ONLY. Checks how the amp behaves with the integration's polling pattern. Changes nothing.

Run on any computer on the LAN:   python3 triad_probe.py 192.168.104.98
"""
import socket
import sys
import time

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.104.98"
PORT = 52000


def get(sock, cmd, out):
    sock.sendall(bytes([0xFF, 0x55, 0x04, 0x03, cmd, 0xF5, out - 1]))
    sock.settimeout(2)
    try:
        return sock.recv(200).decode("latin-1").strip("\x00").strip()
    except Exception:
        return "<no reply>"


print("A) ONE connection, 30 queries (10 rooms x source/volume/mute):")
t = time.time()
s = socket.create_connection((HOST, PORT), timeout=3)
bad = 0
for out in range(1, 11):
    for cmd in (0x1D, 0x1E, 0x17):
        r = get(s, cmd, out)
        if r == "<no reply>" or "Out[" not in r:
            bad += 1
        print("  ", out, hex(cmd), r)
s.close()
print(f"   -> {bad} bad replies, {time.time() - t:.2f}s")

print("B) 5 quick back-to-back sessions:")
for i in range(5):
    s = socket.create_connection((HOST, PORT), timeout=3)
    print("  ", i + 1, get(s, 0x1E, 1))
    s.close()
print("done")
