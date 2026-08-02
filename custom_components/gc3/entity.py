"""Shared base entity for GC3."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_PORT, DOMAIN
from .coordinator import GC3Coordinator


class GC3Entity(CoordinatorEntity[GC3Coordinator]):
    """Base for all GC3 entities: one panel device, name from the entity."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: GC3Coordinator) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        host = entry.data[CONF_HOST]
        port = entry.data.get(CONF_PORT, DEFAULT_PORT)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer="2GIG",
            model="GC3",
            name="2GIG GC3",
            configuration_url=f"https://{host}:{port}",
        )
