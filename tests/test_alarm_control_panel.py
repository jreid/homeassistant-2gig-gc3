"""The alarm_control_panel entity."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.alarm_control_panel import (
    DOMAIN as ALARM_DOMAIN,
)
from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_HOST,
    SERVICE_ALARM_ARM_AWAY,
    SERVICE_ALARM_ARM_HOME,
    SERVICE_ALARM_ARM_NIGHT,
    SERVICE_ALARM_DISARM,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.gc3.const import (
    CONF_BYPASS_NOT_READY,
    CONF_DISARM_PIN,
    CONF_NIGHT_NO_ENTRY_DELAY,
    CONF_NO_ENTRY_DELAY,
    CONF_NO_EXIT_DELAY,
)
from pygc3 import GC3ConnectionError, GC3ResponseError

from .conftest import set_status
from .const import DISARM_PIN, ENTRY_DATA, STATUS_ARMED_STAY

ENTITY_ID = "alarm_control_panel.2gig_gc3"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _call(hass: HomeAssistant, service: str, **extra: object) -> None:
    await hass.services.async_call(
        ALARM_DOMAIN, service, {ATTR_ENTITY_ID: ENTITY_ID, **extra}, blocking=True
    )


async def test_entity_shape(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The panel entity is the device's primary feature and supports 3 arm modes."""
    await _setup(hass, config_entry)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == AlarmControlPanelState.DISARMED
    features = state.attributes["supported_features"]
    assert features == (
        AlarmControlPanelEntityFeature.ARM_HOME
        | AlarmControlPanelEntityFeature.ARM_AWAY
        | AlarmControlPanelEntityFeature.ARM_NIGHT
    )
    # A stored PIN means HA should not prompt for a code.
    assert state.attributes["code_format"] is None


@pytest.mark.parametrize(
    ("service", "expected"),
    [
        (SERVICE_ALARM_ARM_HOME, {"stay": True}),
        (SERVICE_ALARM_ARM_AWAY, {"stay": False}),
        (SERVICE_ALARM_ARM_NIGHT, {"stay": True}),
    ],
)
async def test_arm_services(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    service: str,
    expected: dict,
) -> None:
    """Each arm service maps to the right pygc3 call, with delays left alone."""
    await _setup(hass, config_entry)
    await _call(hass, service)

    kwargs = mock_client.arm.await_args.kwargs
    assert {k: kwargs[k] for k in expected} == expected
    assert kwargs["no_exit_delay"] is False
    assert kwargs["night"] is False
    assert kwargs["bypass_not_ready"] is False


async def test_arm_keeps_entry_delay_by_default(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """No arm mode suppresses the panel's entry delay unless asked to.

    pygc3 maps its `night=` kwarg onto the panel's `noEntryDelay` flag, so
    forwarding it unconditionally made an opened entry zone alarm instantly
    instead of starting the countdown. That is now opt-in per arm mode, and
    both options default off, so an unconfigured entry behaves as before.
    """
    await _setup(hass, config_entry)

    for service in (
        SERVICE_ALARM_ARM_HOME,
        SERVICE_ALARM_ARM_AWAY,
        SERVICE_ALARM_ARM_NIGHT,
    ):
        await _call(hass, service)
        assert mock_client.arm.await_args.kwargs["night"] is False, service


async def test_arm_night_is_remembered(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The panel can't report night back, so the entity must remember it."""
    await _setup(hass, config_entry)
    set_status(mock_client, STATUS_ARMED_STAY)

    await _call(hass, SERVICE_ALARM_ARM_NIGHT)
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID).state == AlarmControlPanelState.ARMED_NIGHT


async def test_arm_home_after_night_is_not_night(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    freezer,
) -> None:
    """Re-arming to home clears the night flag."""
    await _setup(hass, config_entry)
    set_status(mock_client, STATUS_ARMED_STAY)

    await _call(hass, SERVICE_ALARM_ARM_NIGHT)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == AlarmControlPanelState.ARMED_NIGHT

    await _call(hass, SERVICE_ALARM_ARM_HOME)
    # The post-command refresh is debounced, so let the next scheduled poll land.
    await _advance(hass, freezer)

    assert hass.states.get(ENTITY_ID).state == AlarmControlPanelState.ARMED_HOME


async def test_arm_uses_configured_options(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """no_exit_delay / bypass_not_ready options reach the panel."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry,
        options={CONF_NO_EXIT_DELAY: True, CONF_BYPASS_NOT_READY: True},
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    await _call(hass, SERVICE_ALARM_ARM_AWAY)
    kwargs = mock_client.arm.await_args.kwargs
    assert kwargs["no_exit_delay"] is True
    assert kwargs["bypass_not_ready"] is True


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        # Each option reaches its own arm modes and no others. Getting this
        # wrong means either a siren on the way in or no instant night arm.
        (
            {CONF_NO_ENTRY_DELAY: True},
            {
                SERVICE_ALARM_ARM_HOME: True,
                SERVICE_ALARM_ARM_AWAY: True,
                SERVICE_ALARM_ARM_NIGHT: False,
            },
        ),
        (
            {CONF_NIGHT_NO_ENTRY_DELAY: True},
            {
                SERVICE_ALARM_ARM_HOME: False,
                SERVICE_ALARM_ARM_AWAY: False,
                SERVICE_ALARM_ARM_NIGHT: True,
            },
        ),
        (
            {CONF_NO_ENTRY_DELAY: True, CONF_NIGHT_NO_ENTRY_DELAY: True},
            {
                SERVICE_ALARM_ARM_HOME: True,
                SERVICE_ALARM_ARM_AWAY: True,
                SERVICE_ALARM_ARM_NIGHT: True,
            },
        ),
    ],
)
async def test_entry_delay_options_are_per_arm_mode(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    options: dict,
    expected: dict,
) -> None:
    """Home/away and night carry independent entry-delay options."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, options=options)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    for service, no_entry_delay in expected.items():
        await _call(hass, service)
        assert mock_client.arm.await_args.kwargs["night"] is no_entry_delay, service


async def test_night_entry_delay_does_not_change_the_night_label(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The panel flag and the HA-side armed_night label are unrelated.

    They collide on the name `night` in pygc3's signature, so a mix-up would be
    easy and silent: arming night would report armed_home, or arming home would
    report armed_night.
    """
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_NIGHT_NO_ENTRY_DELAY: True}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    set_status(mock_client, STATUS_ARMED_STAY)

    await _call(hass, SERVICE_ALARM_ARM_NIGHT)
    await hass.async_block_till_done()

    assert mock_client.arm.await_args.kwargs["night"] is True
    assert hass.states.get(ENTITY_ID).state == AlarmControlPanelState.ARMED_NIGHT


async def test_disarm_uses_stored_pin(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """With a PIN stored, disarm needs no code from the caller."""
    await _setup(hass, config_entry)
    await _call(hass, SERVICE_ALARM_DISARM)
    mock_client.disarm.assert_awaited_once_with(DISARM_PIN)


async def test_disarm_code_overrides_stored_pin(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """An explicitly supplied code wins over the stored PIN."""
    await _setup(hass, config_entry)
    await _call(hass, SERVICE_ALARM_DISARM, code="9999")
    mock_client.disarm.assert_awaited_once_with("9999")


async def test_disarm_without_any_pin_is_a_validation_error(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    """No stored PIN and no code: tell the user, don't call the panel."""
    entry = MockConfigEntry(
        domain="gc3",
        data={**ENTRY_DATA, CONF_DISARM_PIN: ""},
        unique_id=ENTRY_DATA[CONF_HOST],
    )
    await _setup(hass, entry)

    assert hass.states.get(ENTITY_ID).attributes["code_format"] == "number"
    with pytest.raises(ServiceValidationError):
        await _call(hass, SERVICE_ALARM_DISARM)
    mock_client.disarm.assert_not_awaited()


@pytest.mark.parametrize(
    "error", [GC3ConnectionError("refused"), GC3ResponseError("HTTP 500")]
)
async def test_command_failure_raises_home_assistant_error(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    error: Exception,
) -> None:
    """action-exceptions: panel failures surface as a translated HA error."""
    await _setup(hass, config_entry)
    mock_client.arm.side_effect = error

    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, SERVICE_ALARM_ARM_AWAY)
    assert err.value.translation_key == "command_failed"


async def test_entity_goes_unavailable_when_polling_fails(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    freezer,
) -> None:
    """entity-unavailable: a dead panel must not show a stale armed state."""
    await _setup(hass, config_entry)
    mock_client.status.side_effect = GC3ConnectionError("refused")

    await _advance(hass, freezer)

    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE


async def _advance(hass: HomeAssistant, freezer) -> None:
    """Move past one poll interval and let the coordinator run."""
    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
