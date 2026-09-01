"""Constants for the 2GIG GC3 integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "gc3"

PLATFORMS = [Platform.ALARM_CONTROL_PANEL, Platform.BINARY_SENSOR, Platform.SENSOR]

# Config entry keys (host/port/api_key reuse homeassistant.const equivalents)
CONF_PAIRING_KEY = "pairing_key"
CONF_DISARM_PIN = "disarm_pin"
CONF_PARTITION = "partition"
CONF_POLL_INTERVAL = "poll_interval"
# Split per arm mode: arming home/away instant trips the siren on the way in,
# while an instant night arm is the conventional meaning of night mode.
CONF_NO_ENTRY_DELAY = "no_entry_delay"
CONF_NIGHT_NO_ENTRY_DELAY = "night_no_entry_delay"
CONF_BYPASS_NOT_READY = "bypass_not_ready"

DEFAULT_PORT = 3000
DEFAULT_PARTITION = 1
# The panel is local and cheap to poll, and zone state is the whole point of the
# integration, so poll fast. The bridge has run at 3s for months.
DEFAULT_POLL_INTERVAL = 3
MIN_POLL_INTERVAL = 1
MAX_POLL_INTERVAL = 60
