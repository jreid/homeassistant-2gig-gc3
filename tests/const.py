"""Shared test data, shaped like real captures from the panel."""

from __future__ import annotations

from typing import Any

from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_PORT

from custom_components.gc3.const import (
    CONF_DISARM_PIN,
    CONF_PAIRING_KEY,
    CONF_PARTITION,
)

HOST = "192.168.1.25"
API_KEY = "11111111-2222-3333-4444-555555555555"
PAIRING_KEY = "123456"
DISARM_PIN = "1234"

ENTRY_DATA: dict[str, Any] = {
    CONF_HOST: HOST,
    CONF_API_KEY: API_KEY,
    CONF_PAIRING_KEY: PAIRING_KEY,
    CONF_DISARM_PIN: DISARM_PIN,
    CONF_PORT: 3000,
    CONF_PARTITION: 1,
}

STATUS_READY = {"armState": "ready", "isArmedStay": False}
STATUS_ARMED_AWAY = {"armState": "armed", "isArmedStay": False}
STATUS_ARMED_STAY = {"armState": "armed", "isArmedStay": True}
STATUS_ARMING = {"armState": "arming", "isArmedStay": True}


def zone(
    zone_id: str,
    name: str,
    *,
    open_: bool = False,
    in_alarm: bool = False,
    loss_of_supervision: bool = False,
    battery_low: bool = False,
) -> dict[str, Any]:
    """Build a zone payload in the panel's own shape."""
    return {
        "id": zone_id,
        "deviceSerialNumber": 900000 + int(zone_id),
        "equipmentCode": 2862,
        "voiceDescriptor": name,
        "connectionType": "wireless",
        "zoneAlarmType": 1,
        "zonePhysicalType": 1,
        "partitionAssignment": 1,
        "sensorSupervisionEnabled": True,
        "sensorReportingEnabled": True,
        "status": {
            "open": open_,
            "inAlarm": in_alarm,
            "inAlarmType": 0,
            "batteryLow": battery_low,
            "tampered": False,
            "crossed": False,
            "bypassable": True,
            "bypassed": False,
            "bypassedByUser": 0,
            "bypassType": 0,
            "lossOfSupervision": loss_of_supervision,
            "notReady": False,
        },
    }


ZONES = [
    zone("1", "Mud Room Door"),
    zone("5", "Main Floor Motion"),
    zone("8", "Maintenance Room Flood"),
    zone("9", "Garage Overhead Door West"),
    zone("301", "Keyfob"),
]
