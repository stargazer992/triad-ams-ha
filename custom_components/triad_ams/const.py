"""Constants for the Triad AMS integration."""
from __future__ import annotations

DOMAIN = "triad_ams"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_MODE = "mode"                        # "monitor" (read only, default) or "control"
CONF_OUTPUTS = "outputs"                  # {"1": "Kitchen", ...}    amp output number (1-24) -> room name
CONF_INPUTS = "inputs"                    # {"5": "Streamer", ...}   amp input number (1-24) -> source name
CONF_DEFAULT_INPUT = "default_input"      # input used when a room is switched on without a remembered source
CONF_POLL_SECONDS = "poll_seconds"

MODE_MONITOR = "monitor"                  # never writes to the amp; Control4 keeps full control
MODE_CONTROL = "control"                  # HA drives the amp
DEFAULT_MODE = MODE_MONITOR

DEFAULT_PORT = 52000
EQ_RETRY_SECONDS = 60         # after a failed tone read
EQ_POLL_SECONDS = 300         # bass / treble / balance rarely change: read them every 5 minutes, not every poll
DEFAULT_POLL_SECONDS = 10
MIN_POLL_SECONDS = 3
DEFAULT_INPUT = 1
DEFAULT_ON_VOLUME = 10        # amp volume value (0..100, about -55 dB) used when a room is turned on and has no remembered volume

# Starting suggestion in the setup form only; every install enters its own rooms and sources.
DEFAULT_OUTPUTS = {str(n): f"Room {n}" for n in range(1, 9)}
DEFAULT_INPUTS = {"1": "Input 1"}

SNAPSHOT_STORAGE_VERSION = 1
SNAPSHOT_KEY = f"{DOMAIN}.snapshot"
VOLUME_CURVE_KEY = f"{DOMAIN}.volume_curve"
VOLUME_SEEK_STEPS = 8         # restore: at most this many volume SETs per room to land on the saved dB

SERVICE_SNAPSHOT = "snapshot"
SERVICE_RESTORE = "restore_snapshot"
SERVICE_ALL_OFF = "all_off"
SERVICE_PARTY = "party"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
RECENT_ERRORS = 20            # poll errors kept for the diagnostics download
