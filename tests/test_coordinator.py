"""Coordinator: state mapping, night tracking, and supervision repairs."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.alarm_control_panel import AlarmControlPanelState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.gc3.const import DOMAIN
from custom_components.gc3.coordinator import (
    SUPERVISION_FAILURES_BEFORE_REPAIR,
    map_alarm_state,
)
from pygc3 import PanelStatus, Zone

from .conftest import set_status, set_zones
from .const import (
    STATUS_ARMED_AWAY,
    STATUS_ARMED_STAY,
    STATUS_ARMING,
    STATUS_READY,
    ZONES,
    zone,
)


def _status(raw: dict) -> PanelStatus:
    return PanelStatus.from_json(dict(raw))


def _zones(raw: list[dict]) -> list[Zone]:
    return [Zone.from_json(dict(z)) for z in raw]


@pytest.mark.parametrize(
    ("status", "night", "expected"),
    [
        (STATUS_READY, False, AlarmControlPanelState.DISARMED),
        (STATUS_ARMED_AWAY, False, AlarmControlPanelState.ARMED_AWAY),
        (STATUS_ARMED_STAY, False, AlarmControlPanelState.ARMED_HOME),
        (STATUS_ARMED_STAY, True, AlarmControlPanelState.ARMED_NIGHT),
        (STATUS_ARMING, False, AlarmControlPanelState.ARMING),
        (
            {"armState": "alarm", "isArmedStay": False},
            False,
            AlarmControlPanelState.TRIGGERED,
        ),
    ],
)
async def test_map_alarm_state(
    status: dict, night: bool, expected: AlarmControlPanelState
) -> None:
    """The panel's two fields plus the local night flag cover every HA state."""
    assert map_alarm_state(_status(status), _zones(ZONES), night) == expected


async def test_zone_in_alarm_beats_arm_state() -> None:
    """A sounding zone is TRIGGERED regardless of what armState says."""
    zones = _zones([zone("1", "Mud Room Door", in_alarm=True)])
    assert (
        map_alarm_state(_status(STATUS_ARMED_STAY), zones, False)
        is AlarmControlPanelState.TRIGGERED
    )


async def test_night_flag_clears_when_panel_disarms(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A disarm from the keypad must drop the locally-remembered night state."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = config_entry.runtime_data

    coordinator.note_arm(night=True)
    set_status(mock_client, STATUS_ARMED_STAY)
    await _advance(hass, freezer)
    assert coordinator.alarm_state is AlarmControlPanelState.ARMED_NIGHT

    # Someone punches in their code on the panel itself.
    set_status(mock_client, STATUS_READY)
    await _advance(hass, freezer)
    assert coordinator.alarm_state is AlarmControlPanelState.DISARMED

    # ...and re-arming to stay must not silently come back as night.
    set_status(mock_client, STATUS_ARMED_STAY)
    await _advance(hass, freezer)
    assert coordinator.alarm_state is AlarmControlPanelState.ARMED_HOME


async def test_supervision_repair_issue_lifecycle(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A persistently unheard zone raises a repair, and recovery clears it."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    registry = ir.async_get(hass)
    issue_id = f"{config_entry.entry_id}_supervision_1"

    bad = [zone("1", "Mud Room Door", loss_of_supervision=True)]
    set_zones(mock_client, bad)

    # A brief flap is not worth a repair.
    for _ in range(SUPERVISION_FAILURES_BEFORE_REPAIR - 1):
        await _advance(hass, freezer)
    assert registry.async_get_issue(DOMAIN, issue_id) is None

    await _advance(hass, freezer)
    issue = registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_placeholders == {"zone": "Mud Room Door"}

    set_zones(mock_client, [zone("1", "Mud Room Door")])
    await _advance(hass, freezer)
    assert registry.async_get_issue(DOMAIN, issue_id) is None


async def test_supervision_issue_cleared_when_zone_deleted(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A zone removed on the panel can never clear its own issue, so we do."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    registry = ir.async_get(hass)
    issue_id = f"{config_entry.entry_id}_supervision_1"

    set_zones(mock_client, [zone("1", "Mud Room Door", loss_of_supervision=True)])
    for _ in range(SUPERVISION_FAILURES_BEFORE_REPAIR):
        await _advance(hass, freezer)
    assert registry.async_get_issue(DOMAIN, issue_id) is not None

    set_zones(mock_client, [zone("5", "Main Floor Motion")])
    await _advance(hass, freezer)
    assert registry.async_get_issue(DOMAIN, issue_id) is None


async def _advance(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Move past one poll interval and let the coordinator run."""
    freezer.tick(timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
