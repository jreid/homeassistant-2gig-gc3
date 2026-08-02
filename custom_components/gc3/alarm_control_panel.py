"""Alarm control panel platform for GC3."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
    CodeFormat,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from pygc3 import GC3Error

from .const import CONF_BYPASS_NOT_READY, CONF_DISARM_PIN, CONF_NO_EXIT_DELAY, DOMAIN
from .coordinator import GC3ConfigEntry, GC3Coordinator
from .entity import GC3Entity

# The panel serves a single API session and is stingy about concurrency; never
# let two arm/disarm commands overlap.
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GC3ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the single panel entity."""
    async_add_entities([GC3AlarmPanel(entry.runtime_data)])


class GC3AlarmPanel(GC3Entity, AlarmControlPanelEntity):
    """The panel's arm/disarm entity."""

    _attr_name = None  # primary feature of the device
    _attr_code_arm_required = False
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_HOME
        | AlarmControlPanelEntityFeature.ARM_AWAY
        | AlarmControlPanelEntityFeature.ARM_NIGHT
    )

    def __init__(self, coordinator: GC3Coordinator) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_alarm"
        # With no PIN stored we have to ask for one at disarm time.
        if not entry.data.get(CONF_DISARM_PIN):
            self._attr_code_format = CodeFormat.NUMBER

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        return self.coordinator.alarm_state

    @property
    def _arm_options(self) -> dict[str, bool]:
        options = self.coordinator.config_entry.options
        return {
            "no_exit_delay": options.get(CONF_NO_EXIT_DELAY, False),
            "bypass_not_ready": options.get(CONF_BYPASS_NOT_READY, False),
        }

    async def _command(
        self, action: Callable[[], Awaitable[Any]], *, night: bool
    ) -> None:
        """Issue a panel command, record the night flag, then re-poll.

        `night` is recorded before the refresh is requested because the panel
        cannot report it back -- see `map_alarm_state`.
        """
        try:
            await action()
        except GC3Error as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        self.coordinator.note_arm(night=night)
        await self.coordinator.async_request_refresh()

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        pin = code or self.coordinator.config_entry.data.get(CONF_DISARM_PIN, "")
        if not pin:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_disarm_pin"
            )
        await self._command(lambda: self.coordinator.client.disarm(pin), night=False)

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        await self._command(
            lambda: self.coordinator.client.arm(stay=True, **self._arm_options),
            night=False,
        )

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self._command(
            lambda: self.coordinator.client.arm(stay=False, **self._arm_options),
            night=False,
        )

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        await self._command(
            lambda: self.coordinator.client.arm(
                stay=True, night=True, **self._arm_options
            ),
            night=True,
        )
