"""Config, options, reauth and reconfigure flow tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gc3.const import (
    CONF_BYPASS_NOT_READY,
    CONF_NIGHT_NO_ENTRY_DELAY,
    CONF_NO_ENTRY_DELAY,
    CONF_NO_EXIT_DELAY,
    CONF_PAIRING_KEY,
    CONF_POLL_INTERVAL,
    DOMAIN,
)
from pygc3 import GC3AuthError, GC3ConnectionError, GC3ResponseError

from .const import API_KEY, ENTRY_DATA, HOST


async def test_user_flow_creates_entry(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    """Happy path: credentials authenticate and an entry is created."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], dict(ENTRY_DATA)
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "2GIG GC3"
    assert result["data"][CONF_API_KEY] == API_KEY
    assert result["result"].unique_id == HOST
    # test-before-configure: the flow really talked to the panel.
    assert mock_client.status.await_count >= 1


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (GC3AuthError("Invalid API key"), "invalid_auth"),
        (GC3ConnectionError("timeout"), "cannot_connect"),
        (GC3ResponseError("garbage"), "unknown"),
    ],
)
async def test_user_flow_errors_then_recovers(
    hass: HomeAssistant, mock_client: AsyncMock, error: Exception, expected: str
) -> None:
    """A failing check shows the form again, and retrying succeeds."""
    mock_client.status.side_effect = error
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], dict(ENTRY_DATA)
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}

    mock_client.status.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], dict(ENTRY_DATA)
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_aborts_when_host_already_configured(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """unique-config-entry: the same panel cannot be added twice."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], dict(ENTRY_DATA)
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_flow_updates_credentials(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Reauth replaces the keys and keeps the rest of the entry data."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    mock_client.status.side_effect = GC3AuthError("Invalid API key")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "bad", CONF_PAIRING_KEY: "000000"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    mock_client.status.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_API_KEY: "new-key", CONF_PAIRING_KEY: "654321"},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_API_KEY] == "new-key"
    assert config_entry.data[CONF_PAIRING_KEY] == "654321"
    assert config_entry.data[CONF_HOST] == HOST


async def test_reconfigure_flow_changes_host(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Reconfigure can move the panel to a new port without re-adding it."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await config_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**ENTRY_DATA, CONF_PORT: 3001}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data[CONF_PORT] == 3001


async def test_reconfigure_flow_rejects_a_different_panel(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Pointing an entry at a different host is a mistake, not a move."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**ENTRY_DATA, CONF_HOST: "192.168.1.99"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_panel"


async def test_reconfigure_flow_surfaces_errors(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """A panel that stops answering keeps the reconfigure form open."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_client.status.side_effect = GC3ConnectionError("refused")
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], dict(ENTRY_DATA)
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_options_flow(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Options are stored and the entry reloads with the new poll interval."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_POLL_INTERVAL: 15,
            CONF_NO_EXIT_DELAY: True,
            CONF_NO_ENTRY_DELAY: True,
            CONF_NIGHT_NO_ENTRY_DELAY: True,
            CONF_BYPASS_NOT_READY: True,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options[CONF_POLL_INTERVAL] == 15
    assert config_entry.options[CONF_NO_ENTRY_DELAY] is True
    assert config_entry.options[CONF_NIGHT_NO_ENTRY_DELAY] is True
    coordinator = config_entry.runtime_data
    assert coordinator.update_interval.total_seconds() == 15


async def test_options_flow_rejects_out_of_range_interval(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The poll interval is bounded so a typo can't stall or hammer the panel."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_POLL_INTERVAL: 9999}
        )
