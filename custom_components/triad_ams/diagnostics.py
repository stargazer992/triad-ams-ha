"""Download diagnostics: mode, what the amp reports, the snapshot, the learned volume points and recent errors."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_HOST
from .coordinator import TriadConfigEntry

TO_REDACT = {CONF_HOST}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: TriadConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    outputs = {
        str(out): {
            "name": name,
            **(
                {"source": s.source, "volume_value": s.volume_value, "volume_db": s.volume_db, "muted": s.muted}
                if (s := (coordinator.data or {}).get(out)) is not None
                else {"available": False}
            ),
            **coordinator.eq.get(out, {}),
        }
        for out, name in sorted(coordinator.outputs.items())
    }
    return {
        "entry": {"data": async_redact_data(dict(entry.data), TO_REDACT), "options": dict(entry.options)},
        "mode": coordinator.mode,
        "last_update_success": coordinator.last_update_success,
        "last_exception": None if coordinator.last_exception is None else str(coordinator.last_exception),
        "recent_errors": list(coordinator.recent_errors),
        "outputs": outputs,
        "snapshot": await coordinator.async_saved_snapshot(),
        "learned_volume_points": {str(v): db for v, db in sorted(coordinator.client.curve.learned.items())},
    }
