"""Typed models for the GC3 local automation API.

These are transport models: they mirror what the panel returns, normalised into
snake_case with sane Python types. Deliberately *not* included: the mapping to
Home Assistant alarm states (armed_home/away/night/triggered), which is stateful
(night is not observable from the panel) and cross-cuts status + zones -- that
belongs to the consumer, not the wire library.

Every model keeps the untouched `raw` dict so a consumer can reach a field we
didn't model without a library change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _as_bool(value: Any) -> bool:
    """Coerce the panel's truthiness (bool, 0/1, "true") to a real bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


@dataclass(frozen=True, slots=True)
class PanelStatus:
    """`GET /api/v1/status` -- the panel reports only these two fields."""

    arm_state: str  # raw armState, lowercased: ready/arming/armed/... (see notes)
    is_armed_stay: bool
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PanelStatus:
        return cls(
            arm_state=str(data.get("armState") or "").lower(),
            is_armed_stay=_as_bool(data.get("isArmedStay")),
            raw=data,
        )


@dataclass(frozen=True, slots=True)
class ZoneStatus:
    """The `status` sub-object of a zone."""

    open: bool
    in_alarm: bool
    battery_low: bool
    tampered: bool
    bypassed: bool
    bypassable: bool
    loss_of_supervision: bool
    not_ready: bool
    crossed: bool
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ZoneStatus:
        return cls(
            open=_as_bool(data.get("open")),
            in_alarm=_as_bool(data.get("inAlarm")),
            battery_low=_as_bool(data.get("batteryLow")),
            tampered=_as_bool(data.get("tampered")),
            bypassed=_as_bool(data.get("bypassed")),
            bypassable=_as_bool(data.get("bypassable")),
            loss_of_supervision=_as_bool(data.get("lossOfSupervision")),
            not_ready=_as_bool(data.get("notReady")),
            crossed=_as_bool(data.get("crossed")),
            raw=data,
        )


@dataclass(frozen=True, slots=True)
class Zone:
    """One entry from `GET /api/v1/zones`."""

    id: str
    name: str  # voiceDescriptor
    serial: int | None  # deviceSerialNumber
    equipment_code: int | None
    connection_type: str | None
    partition: int | None
    zone_physical_type: int | None
    zone_alarm_type: int | None
    supervision_enabled: bool
    status: ZoneStatus
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Zone:
        serial = data.get("deviceSerialNumber")
        return cls(
            id=str(data.get("id")),
            name=data.get("voiceDescriptor") or f"Zone {data.get('id')}",
            serial=int(serial) if serial is not None else None,
            equipment_code=data.get("equipmentCode"),
            connection_type=data.get("connectionType"),
            partition=data.get("partitionAssignment"),
            zone_physical_type=data.get("zonePhysicalType"),
            zone_alarm_type=data.get("zoneAlarmType"),
            supervision_enabled=_as_bool(data.get("sensorSupervisionEnabled")),
            status=ZoneStatus.from_json(data.get("status") or {}),
            raw=data,
        )

    @property
    def is_console(self) -> bool:
        """Zone 0 / the panel's own console -- not a real sensor to expose."""
        return self.id == "0" or self.name.strip().lower() == "console"


@dataclass(frozen=True, slots=True)
class Pairing:
    """`GET /api/v1/pair` -- the controller currently enrolled on the panel."""

    pairing_key: str
    device_name: str
    all_partition_access: bool
    is_security_device: bool
    member_of_partition: str
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Pairing:
        return cls(
            pairing_key=str(data.get("PairingKey") or ""),
            device_name=str(data.get("deviceName") or ""),
            all_partition_access=_as_bool(data.get("allPartitionAccess")),
            is_security_device=_as_bool(data.get("isSecurityDevice")),
            member_of_partition=str(data.get("memberOfPartition") or ""),
            raw=data,
        )
