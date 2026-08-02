"""Zone binary sensors, including zones that come and go."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.gc3.binary_sensor import zone_device_class
from pygc3 import GC3ConnectionError

from .conftest import set_zones
from .const import ZONES, zone

MUD_ROOM = "binary_sensor.2gig_gc3_mud_room_door"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Mud Room Door", BinarySensorDeviceClass.DOOR),
        ("Front Window", BinarySensorDeviceClass.WINDOW),
        ("Garage Overhead Door West", BinarySensorDeviceClass.GARAGE_DOOR),
        ("Main Floor Motion", BinarySensorDeviceClass.MOTION),
        ("Maintenance Room Flood", BinarySensorDeviceClass.MOISTURE),
        ("Living Room Glass Break", BinarySensorDeviceClass.VIBRATION),
        ("Basement Smoke", BinarySensorDeviceClass.SMOKE),
        ("Hallway Carbon Monoxide", BinarySensorDeviceClass.CO),
        ("Attic Freeze", BinarySensorDeviceClass.COLD),
        ("Furnace Heat", BinarySensorDeviceClass.HEAT),
        ("Patio Slider", BinarySensorDeviceClass.DOOR),
        ("Zone 12", BinarySensorDeviceClass.OPENING),
        ("Keyfob", None),
    ],
)
async def test_zone_device_class(
    name: str, expected: BinarySensorDeviceClass | None
) -> None:
    """Descriptor-to-device-class mapping, ported from the bridge."""
    assert zone_device_class(name) == expected


async def test_zone_entities_created(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """One entity per zone, named from the panel's voice descriptor."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, config_entry.entry_id)
    zone_entries = [e for e in entries if e.domain == "binary_sensor"]
    assert len(zone_entries) == len(ZONES)

    state = hass.states.get(MUD_ROOM)
    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes["device_class"] == BinarySensorDeviceClass.DOOR
    assert state.attributes["battery_low"] is False
    assert state.attributes["connection"] == "wireless"


async def test_keyfob_zone_is_disabled_by_default(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Keyfobs are momentary and noisy; they shouldn't clutter the UI."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "binary_sensor", "gc3", f"{config_entry.entry_id}_zone_301"
    )
    assert entity_id is not None
    entry = registry.async_get(entity_id)
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(entity_id) is None


async def test_zone_state_follows_the_panel(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    freezer,
) -> None:
    """Opening a door on the panel flips the sensor on the next poll."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(MUD_ROOM).state == STATE_OFF

    set_zones(mock_client, [zone("1", "Mud Room Door", open_=True), *ZONES[1:]])
    await _advance(hass, freezer)

    assert hass.states.get(MUD_ROOM).state == STATE_ON


async def test_zone_added_at_runtime(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    freezer,
) -> None:
    """dynamic-devices: a zone enrolled on the panel shows up without a reload."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.2gig_gc3_shed_door") is None

    set_zones(mock_client, [*ZONES, zone("11", "Shed Door", open_=True)])
    await _advance(hass, freezer)

    state = hass.states.get("binary_sensor.2gig_gc3_shed_door")
    assert state is not None
    assert state.state == STATE_ON


async def test_zone_removed_at_runtime(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    freezer,
) -> None:
    """stale-devices: a zone deleted on the panel is dropped from the registry."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(MUD_ROOM) is not None

    set_zones(mock_client, ZONES[1:])
    await _advance(hass, freezer)

    assert hass.states.get(MUD_ROOM) is None
    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id(
            "binary_sensor", "gc3", f"{config_entry.entry_id}_zone_1"
        )
        is None
    )


async def test_zone_unavailable_when_polling_fails(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    freezer,
) -> None:
    """A zone's last-known state must not linger once the panel goes quiet."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_client.zones.side_effect = GC3ConnectionError("refused")
    await _advance(hass, freezer)

    assert hass.states.get(MUD_ROOM).state == STATE_UNAVAILABLE


async def _advance(hass: HomeAssistant, freezer) -> None:
    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
