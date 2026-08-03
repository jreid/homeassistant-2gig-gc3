"""The open-zone count, which is what gates arming."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from pygc3 import GC3ConnectionError

from .conftest import set_zones
from .const import ZONES, zone

OPEN_ZONES = "sensor.2gig_gc3_open_zones"


async def test_open_zones_starts_at_zero(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """A closed-up house counts nothing open."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(OPEN_ZONES)
    assert state is not None
    assert state.state == "0"
    assert state.attributes["zones"] == []
    assert state.attributes["unit_of_measurement"] == "zones"


async def test_open_zones_counts_openings_only(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    freezer,
) -> None:
    """Motion, flood and keyfob zones are open all the time; they aren't doors."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    set_zones(
        mock_client,
        [
            zone("1", "Mud Room Door", open_=True),
            zone("5", "Main Floor Motion", open_=True),
            zone("8", "Maintenance Room Flood", open_=True),
            zone("9", "Garage Overhead Door West", open_=True),
            zone("301", "Keyfob", open_=True),
        ],
    )
    await _advance(hass, freezer)

    state = hass.states.get(OPEN_ZONES)
    assert state.state == "2"
    assert state.attributes["zones"] == ["Mud Room Door", "Garage Overhead Door West"]


async def test_open_zones_unavailable_when_polling_fails(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    freezer,
) -> None:
    """A stale zero would say the house is shut when we don't know that."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(OPEN_ZONES).state == "0"

    mock_client.zones.side_effect = GC3ConnectionError("refused")
    await _advance(hass, freezer)

    assert hass.states.get(OPEN_ZONES).state == STATE_UNAVAILABLE


async def test_open_zones_follows_the_panel(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    freezer,
) -> None:
    """Opening and closing a door moves the count both ways."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    set_zones(mock_client, [zone("1", "Mud Room Door", open_=True), *ZONES[1:]])
    await _advance(hass, freezer)
    assert hass.states.get(OPEN_ZONES).state == "1"

    set_zones(mock_client, ZONES)
    await _advance(hass, freezer)
    assert hass.states.get(OPEN_ZONES).state == "0"


async def _advance(hass: HomeAssistant, freezer) -> None:
    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
