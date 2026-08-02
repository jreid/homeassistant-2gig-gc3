"""Diagnostics support for the 2GIG GC3 integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

from .const import CONF_DISARM_PIN, CONF_PAIRING_KEY
from .coordinator import GC3ConfigEntry

# Credentials and the disarm PIN are secrets; the host is a private-LAN address
# but still identifying, and zone serials identify the physical sensors.
TO_REDACT_ENTRY = {CONF_API_KEY, CONF_PAIRING_KEY, CONF_DISARM_PIN, "host"}
TO_REDACT_ZONE = {"deviceSerialNumber"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GC3ConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT_ENTRY),
            "options": dict(entry.options),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
            "alarm_state": coordinator.alarm_state,
        },
        # `raw` is the panel's untouched JSON, which is what makes a bug report
        # actionable -- schema drift is the thing we can't guess at.
        "status": data.status.raw if data else None,
        "zones": [async_redact_data(zone.raw, TO_REDACT_ZONE) for zone in data.zones]
        if data
        else [],
    }
