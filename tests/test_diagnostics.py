"""Diagnostics must be useful without leaking credentials."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.const import CONF_API_KEY, CONF_HOST
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.gc3.const import CONF_DISARM_PIN, CONF_PAIRING_KEY

from .const import API_KEY, DISARM_PIN, HOST, PAIRING_KEY

REDACTED = "**REDACTED**"


async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """Secrets are redacted; the raw panel payloads are kept for debugging."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)

    entry_data = result["entry"]["data"]
    for key in (CONF_API_KEY, CONF_PAIRING_KEY, CONF_DISARM_PIN, CONF_HOST):
        assert entry_data[key] == REDACTED

    dumped = str(result)
    for secret in (API_KEY, PAIRING_KEY, DISARM_PIN, HOST):
        assert secret not in dumped

    assert result["coordinator"]["last_update_success"] is True
    assert result["coordinator"]["alarm_state"] == "disarmed"
    assert result["status"] == {"armState": "ready", "isArmedStay": False}

    zones = result["zones"]
    assert len(zones) == 5
    assert zones[0]["voiceDescriptor"] == "Mud Room Door"
    # Sensor serials identify physical hardware; they add nothing to a bug report.
    assert zones[0]["deviceSerialNumber"] == REDACTED
    assert zones[0]["status"]["open"] is False
