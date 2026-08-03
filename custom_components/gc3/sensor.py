"""Sensor platform for GC3 panel-wide counts."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .binary_sensor import is_opening_zone
from .coordinator import GC3ConfigEntry, GC3Coordinator
from .entity import GC3Entity

# Served from the coordinator's snapshot; never touches the panel.
PARALLEL_UPDATES = 0

OPEN_ZONES = SensorEntityDescription(
    key="open_zones",
    translation_key="open_zones",
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement="zones",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GC3ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the panel's summary sensors."""
    async_add_entities([GC3OpenZonesSensor(entry.runtime_data, OPEN_ZONES)])


class GC3OpenZonesSensor(GC3Entity, SensorEntity):
    """How many ways into the house are open right now.

    Counts openings only -- a tripped motion detector doesn't stop you arming,
    an open door does. The `zones` attribute is what turns "arming will fail"
    into "the deck door is open".
    """

    def __init__(
        self, coordinator: GC3Coordinator, description: SensorEntityDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{description.key}"

    @property
    def _open_zones(self) -> list[str]:
        if self.coordinator.data is None:
            return []
        return [
            zone.name
            for zone in self.coordinator.data.zones
            if zone.status.open and is_opening_zone(zone.name)
        ]

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return len(self._open_zones)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {"zones": self._open_zones}
