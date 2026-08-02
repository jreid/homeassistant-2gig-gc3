"""The 2GIG GC3 integration."""

from __future__ import annotations

from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from pygc3 import GC3Client

from .const import (
    CONF_PAIRING_KEY,
    CONF_PARTITION,
    CONF_POLL_INTERVAL,
    DEFAULT_PARTITION,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PORT,
    PLATFORMS,
)
from .coordinator import GC3ConfigEntry, GC3Coordinator


async def async_setup_entry(hass: HomeAssistant, entry: GC3ConfigEntry) -> bool:
    """Set up GC3 from a config entry."""
    client = GC3Client(
        entry.data[CONF_HOST],
        entry.data[CONF_PAIRING_KEY],
        entry.data[CONF_API_KEY],
        port=entry.data.get(CONF_PORT, DEFAULT_PORT),
        partition=entry.data.get(CONF_PARTITION, DEFAULT_PARTITION),
        session=async_get_clientsession(hass, verify_ssl=False),
    )
    coordinator = GC3Coordinator(
        hass,
        entry,
        client,
        entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
    )
    # Raises ConfigEntryNotReady / ConfigEntryAuthFailed on the first fetch.
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GC3ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: GC3ConfigEntry) -> None:
    """Reload when options (e.g. poll interval) change."""
    await hass.config_entries.async_reload(entry.entry_id)
