"""Binary sensor platform for GC3 zones."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from pygc3 import Zone

from .coordinator import GC3ConfigEntry, GC3Coordinator
from .entity import GC3Entity

# Reads are served from the coordinator's snapshot, so zone entities never hit
# the panel themselves.
PARALLEL_UPDATES = 0

# Zones whose descriptor matches these are real devices but not openings: they
# are noisy, momentary, and rarely wanted on a dashboard.
_TRANSIENT_KEYWORDS = ("keyfob", "key fob", "fob", "pendant")


def zone_device_class(name: str) -> BinarySensorDeviceClass | None:
    """Map a zone's voice descriptor to a device class.

    Ported from the bridge. Order matters: garage-overhead before the generic
    door check, keyfob before everything (it's not an opening).
    """
    n = name.lower()
    if any(k in n for k in _TRANSIENT_KEYWORDS):
        return None
    if any(k in n for k in ("motion", "pir", "occup")):
        return BinarySensorDeviceClass.MOTION
    if any(k in n for k in ("flood", "leak", "water", "moisture", "sump")):
        return BinarySensorDeviceClass.MOISTURE
    if "glass" in n:
        return BinarySensorDeviceClass.VIBRATION
    if "smoke" in n or "fire" in n:
        return BinarySensorDeviceClass.SMOKE
    if "co " in n or n.endswith(" co") or "carbon" in n:
        return BinarySensorDeviceClass.CO
    if "freeze" in n or "cold" in n:
        return BinarySensorDeviceClass.COLD
    if "heat" in n:
        return BinarySensorDeviceClass.HEAT
    if "overhead" in n or "garage door" in n:
        return BinarySensorDeviceClass.GARAGE_DOOR
    if "window" in n:
        return BinarySensorDeviceClass.WINDOW
    if any(k in n for k in ("door", "slider", "gate")):
        return BinarySensorDeviceClass.DOOR
    return BinarySensorDeviceClass.OPENING


def is_transient_zone(name: str) -> bool:
    """True for keyfobs/pendants, which we create disabled by default."""
    return any(k in name.lower() for k in _TRANSIENT_KEYWORDS)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GC3ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up zone sensors, and keep them in sync as the panel's zones change."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _sync_zones() -> None:
        if coordinator.data is None:
            return
        current = set(coordinator.data.by_id)

        if new := current - known:
            known.update(new)
            async_add_entities(
                GC3ZoneSensor(coordinator, zone_id)
                for zone_id in sorted(new, key=_sort_key)
            )

        if removed := known - current:
            known.difference_update(removed)
            registry = er.async_get(hass)
            for zone_id in removed:
                unique_id = zone_unique_id(entry.entry_id, zone_id)
                if entity_id := registry.async_get_entity_id(
                    "binary_sensor", entry.domain, unique_id
                ):
                    registry.async_remove(entity_id)

    _sync_zones()
    entry.async_on_unload(coordinator.async_add_listener(_sync_zones))


def _sort_key(zone_id: str) -> tuple[int, str]:
    """Numeric zone ids in numeric order, anything else after them."""
    return (int(zone_id), "") if zone_id.isdigit() else (10**9, zone_id)


def zone_unique_id(entry_id: str, zone_id: str) -> str:
    """Stable unique_id for a zone's binary sensor."""
    return f"{entry_id}_zone_{zone_id}"


class GC3ZoneSensor(GC3Entity, BinarySensorEntity):
    """One zone's open/closed state, with panel status flags as attributes."""

    def __init__(self, coordinator: GC3Coordinator, zone_id: str) -> None:
        super().__init__(coordinator)
        self._zone_id = zone_id
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = zone_unique_id(entry_id, zone_id)
        zone = self._zone()
        name = zone.name if zone else f"Zone {zone_id}"
        self._attr_name = name
        if (device_class := zone_device_class(name)) is not None:
            self._attr_device_class = device_class
        if is_transient_zone(name):
            self._attr_entity_registry_enabled_default = False

    def _zone(self) -> Zone | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.by_id.get(self._zone_id)

    @property
    def available(self) -> bool:
        """Unavailable if the panel stopped listing this zone mid-session."""
        return super().available and self._zone() is not None

    @property
    def is_on(self) -> bool | None:
        zone = self._zone()
        return zone.status.open if zone else None

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        zone = self._zone()
        if zone is None:
            return None
        status = zone.status
        return {
            "in_alarm": status.in_alarm,
            "battery_low": status.battery_low,
            "tampered": status.tampered,
            "bypassed": status.bypassed,
            "loss_of_supervision": status.loss_of_supervision,
            "connection": zone.connection_type,
        }
