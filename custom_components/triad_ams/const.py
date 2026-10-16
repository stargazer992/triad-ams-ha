"""Constants for the Triad AMS integration."""
from __future__ import annotations

DOMAIN = "triad_ams"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_MODE = "mode"                        # "monitor" (read only, default) or "control"
CONF_OUTPUTS = "outputs"                  # {"1": "Living Room", ...} amp output number (1-24) -> room name
CONF_INPUTS = "inputs"                    # {"5": "WiiM Ultra", ...} amp input number (1-24) -> source name
CONF_DEFAULT_INPUT = "default_input"      # input used when a room is switched on without a remembered source
CONF_POLL_SECONDS = "poll_seconds"

MODE_MONITOR = "monitor"                  # never writes to the amp; Control4 keeps full control
MODE_CONTROL = "control"                  # HA drives the amp
DEFAULT_MODE = MODE_MONITOR

DEFAULT_PORT = 52000
DEFAULT_POLL_SECONDS = 10
MIN_POLL_SECONDS = 3
DEFAULT_INPUT = 5

# Outputs verified on 2026-10-14 by nudging each room's volume (see docs/protocol.md).
DEFAULT_OUTPUTS = {
    "1": "Living Room",
    "2": "Master Bedroom",
    "3": "Master Laundry",
    "4": "Her Bathroom",
    "5": "Kitchen",
    "6": "Dining",
    "7": "Family Room",
    "8": "Patio",
    "9": "Pool",
    "10": "Bar",
}
DEFAULT_INPUTS = {"5": "WiiM Ultra"}

SNAPSHOT_STORAGE_VERSION = 1
SNAPSHOT_KEY = f"{DOMAIN}.snapshot"

SERVICE_SNAPSHOT = "snapshot"
SERVICE_RESTORE = "restore_snapshot"
SERVICE_ALL_OFF = "all_off"
SERVICE_PARTY = "party"
