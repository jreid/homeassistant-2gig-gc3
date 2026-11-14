"""Data update coordinator for the GC3 panel."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from homeassistant.components.alarm_control_panel import AlarmControlPanelState
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from pygc3 import (
    GC3AuthError,
    GC3Client,
    GC3ConnectionError,
    GC3ResponseError,
    PanelStatus,
    Zone,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

type GC3ConfigEntry = ConfigEntry[GC3Coordinator]

# A zone must report lossOfSupervision for this many consecutive polls before we
# surface a repair issue. The panel flaps the flag briefly when a sensor misses a
# single check-in, which is not worth bothering the user about.
SUPERVISION_FAILURES_BEFORE_REPAIR = 20


@dataclass(slots=True)
class GC3Data:
    """Snapshot of one poll."""

    status: PanelStatus
    zones: list[Zone]
    by_id: dict[str, Zone] = field(init=False)

    def __post_init__(self) -> None:
        self.by_id = {z.id: z for z in self.zones}


# armState strings that mean the panel is disarmed. A door left open while
# disarmed is still a disarmed panel, so anything containing these is disarmed.
_DISARMED_MARKERS = ("ready", "inactive", "disarm")


def is_disarmed_state(arm_state: str) -> bool:
    """True for the panel's disarmed armState values (ready/inactive/...)."""
    return any(marker in arm_state for marker in _DISARMED_MARKERS)


def map_alarm_state(
    status: PanelStatus,
    zones: list[Zone],
    armed_night: bool,
    was_armed: bool = False,
) -> AlarmControlPanelState:
    """Derive the HA alarm state.

    Mirrors the bridge's mapping. `night` is not observable from the panel, so it
    is remembered locally (see coordinator) and only distinguishes armed_night
    from armed_home while the panel reports armed+stay.

    The panel's entry-delay countdown reports an armState we have not captured.
    It used to fall through to DISARMED, which showed a counting-down panel as
    disarmed (2026-11-13: garage man door opened while armed away -> HA said
    "disarmed", 45s later the siren sounded). An unrecognised armState right
    after the panel was armed is now PENDING; the coordinator logs the raw value.
    """
    if any(z.status.in_alarm for z in zones):
        return AlarmControlPanelState.TRIGGERED
    state = status.arm_state
    if state == "armed":
        if not status.is_armed_stay:
            return AlarmControlPanelState.ARMED_AWAY
        return (
            AlarmControlPanelState.ARMED_NIGHT
            if armed_night
            else AlarmControlPanelState.ARMED_HOME
        )
    if state == "arming":
        return AlarmControlPanelState.ARMING
    if "alarm" in state:
        return AlarmControlPanelState.TRIGGERED
    if is_disarmed_state(state):
        return AlarmControlPanelState.DISARMED
    if was_armed or "entry" in state or "pending" in state:
        return AlarmControlPanelState.PENDING
    return AlarmControlPanelState.DISARMED


class GC3Coordinator(DataUpdateCoordinator[GC3Data]):
    """Polls /status + /zones and tracks the locally-inferred night state."""

    config_entry: GC3ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: GC3ConfigEntry,
        client: GC3Client,
        poll_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=poll_interval),
        )
        self.client = client
        self._armed_night = False
        # Whether the panel was last seen armed. An unknown armState after an
        # armed one is the entry delay (see map_alarm_state). Cleared only by a
        # recognised disarmed state, so it survives the countdown itself.
        self._was_armed = False
        self._unknown_states_logged: set[str] = set()
        self._supervision_misses: dict[str, int] = {}
        self._supervision_reported: set[str] = set()

    async def _async_update_data(self) -> GC3Data:
        try:
            status = await self.client.status()
            zones = await self.client.zones()
        except GC3AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (GC3ConnectionError, GC3ResponseError) as err:
            raise UpdateFailed(str(err)) from err
        arm_state = status.arm_state
        # Only a fully armed panel can enter an entry delay. The exit countdown
        # reports "arming" (measured) and maps to ARMING on its own; counting it
        # here would let an unknown exit-side value read as PENDING.
        if arm_state == "armed":
            self._was_armed = True
        elif is_disarmed_state(arm_state):
            # A disarm from any source (keypad, automation) clears the night
            # flag. An entry delay or alarm does not: the panel is still armed.
            self._was_armed = False
            self._armed_night = False
        elif "alarm" not in arm_state and arm_state not in self._unknown_states_logged:
            self._unknown_states_logged.add(arm_state)
            _LOGGER.warning(
                "GC3 reported unrecognised armState %r (raw status %s); treating it"
                " as %s",
                arm_state,
                status.raw,
                "pending" if self._was_armed else "disarmed",
            )
        self._update_supervision_issues(zones)
        return GC3Data(status=status, zones=zones)

    def note_arm(self, *, night: bool) -> None:
        """Record that we issued an arm, so armed_night can be reported."""
        self._armed_night = night

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        """Current HA alarm state, or None before the first successful poll."""
        if self.data is None:
            return None
        return map_alarm_state(
            self.data.status, self.data.zones, self._armed_night, self._was_armed
        )

    def _update_supervision_issues(self, zones: list[Zone]) -> None:
        """Raise/clear a repair issue for zones the panel has stopped hearing from."""
        present = {z.id for z in zones}
        for zone in zones:
            if zone.status.loss_of_supervision:
                self._supervision_misses[zone.id] = (
                    self._supervision_misses.get(zone.id, 0) + 1
                )
            else:
                self._supervision_misses.pop(zone.id, None)

            over_threshold = (
                self._supervision_misses.get(zone.id, 0)
                >= SUPERVISION_FAILURES_BEFORE_REPAIR
            )
            if over_threshold and zone.id not in self._supervision_reported:
                self._supervision_reported.add(zone.id)
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    self._supervision_issue_id(zone.id),
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="zone_supervision_lost",
                    translation_placeholders={"zone": zone.name},
                )
            elif not over_threshold and zone.id in self._supervision_reported:
                self._clear_supervision_issue(zone.id)

        # A zone deleted on the panel can never recover its own issue.
        for zone_id in self._supervision_reported - present:
            self._clear_supervision_issue(zone_id)

    def _clear_supervision_issue(self, zone_id: str) -> None:
        self._supervision_reported.discard(zone_id)
        ir.async_delete_issue(self.hass, DOMAIN, self._supervision_issue_id(zone_id))

    def _supervision_issue_id(self, zone_id: str) -> str:
        return f"{self.config_entry.entry_id}_supervision_{zone_id}"
