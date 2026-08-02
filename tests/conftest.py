"""Fixtures for the GC3 integration tests.

The panel is mocked at the pygc3 boundary rather than at HTTP: pygc3 has its own
transport tests, and everything the integration does is a function of the parsed
models.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gc3.const import DOMAIN
from pygc3 import PanelStatus, Zone

from .const import ENTRY_DATA, HOST, STATUS_READY, ZONES

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    """Let HA load custom_components/gc3 in every test."""
    yield


@pytest.fixture
def mock_client() -> Generator[AsyncMock]:
    """A GC3Client whose reads return the fixture panel, patched everywhere."""
    client = AsyncMock()
    client.status.return_value = PanelStatus.from_json(dict(STATUS_READY))
    client.zones.return_value = [Zone.from_json(dict(z)) for z in ZONES]
    client.arm.return_value = {}
    client.disarm.return_value = {}

    with (
        patch("custom_components.gc3.GC3Client", return_value=client),
        patch("custom_components.gc3.config_flow.GC3Client", return_value=client),
    ):
        yield client


def set_status(client: AsyncMock, status: dict[str, Any]) -> None:
    """Point the mocked client at a different panel status."""
    client.status.return_value = PanelStatus.from_json(dict(status))


def set_zones(client: AsyncMock, zones: list[dict[str, Any]]) -> None:
    """Point the mocked client at a different zone list."""
    client.zones.return_value = [Zone.from_json(dict(z)) for z in zones]


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """An already-configured GC3 entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="2GIG GC3",
        data=dict(ENTRY_DATA),
        unique_id=HOST,
        entry_id="01JGC3TESTENTRYID0000000",
    )
