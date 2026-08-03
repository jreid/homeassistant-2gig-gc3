"""Binary sensor platform for GC3 zones and panel-wide health."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from pygc3 import Zone, ZoneStatus

from .coordinator import GC3ConfigEntry, GC3Coordinator
from .entity import GC3Entity

# Reads are served from the coordinator's snapshot, so zone entities never hit
# the panel themselves.
PARALLEL_UPDATES = 0

# Zones whose descriptor matches these are real devices but not openings: they
# are noisy, momentary, and rarely wanted on a dashboard.
_TRANSIENT_KEYWORDS = ("keyfob", "key fob", "fob", "pendant")

# What counts as "the house is open" for the open-zone count: an opening someone
# can walk through, not a motion, smoke or flood detector.
OPENING_DEVICE_CLASSES = frozenset(
    {
        BinarySensorDeviceClass.DOOR,
        BinarySensorDeviceClass.WINDOW,
        BinarySensorDeviceClass.GARAGE_DOOR,
        BinarySensorDeviceClass.OPENING,
    }
)


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


def is_opening_zone(name: str) -> bool:
    """True for zones that represent a way into the house."""
    return zone_device_class(name) in OPENING_DEVICE_CLASSES


@dataclass(frozen=True, kw_only=True)
class GC3HealthDescription(BinarySensorEntityDescription):
    """A panel-wide roll-up of one zone status flag."""

    flag: Callable[[ZoneStatus], bool]


# One entity per fault the panel can report against a sensor. Each is on when
# any zone raises the flag, and lists the offending zones in `zones` -- which is
# the part that makes a notification actionable.
HEALTH_SENSORS: tuple[GC3HealthDescription, ...] = (
    GC3HealthDescription(
        key="battery_low",
        translation_key="battery_low",
        device_class=BinarySensorDeviceClass.BATTERY,
        flag=lambda status: status.battery_low,
    ),
    GC3HealthDescription(
        key="tamper",
        translation_key="tamper",
        device_class=BinarySensorDeviceClass.TAMPER,
        flag=lambda status: status.tampered,
    ),
    # A supervised sensor that stops checking in is indistinguishable from one
    # that has been removed or jammed -- worth surfacing apart from a low battery.
    GC3HealthDescription(
        key="supervision_lost",
        translation_key="supervision_lost",
        device_class=BinarySensorDeviceClass.PROBLEM,
        flag=lambda status: status.loss_of_supervision,
    ),
    # Bypassed zones are armed-but-ignored. Easy to leave set by accident.
    GC3HealthDescription(
        key="zones_bypassed",
        translation_key="zones_bypassed",
        device_class=BinarySensorDeviceClass.PROBLEM,
        flag=lambda status: status.bypassed,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GC3ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up zone sensors, and keep them in sync as the panel's zones change."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    async_add_entities(
        GC3HealthSensor(coordinator, description) for description in HEALTH_SENSORS
    )

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


class GC3HealthSensor(GC3Entity, BinarySensorEntity):
    """Panel-wide health: on when any zone raises the described flag."""

    entity_description: GC3HealthDescription
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: GC3Coordinator, description: GC3HealthDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{description.key}"

    @property
    def _affected_zones(self) -> list[str]:
        """Names of the zones currently raising this flag, in panel order."""
        if self.coordinator.data is None:
            return []
        return [
            zone.name
            for zone in self.coordinator.data.zones
            if self.entity_description.flag(zone.status)
        ]

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return bool(self._affected_zones)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {"zones": self._affected_zones}
