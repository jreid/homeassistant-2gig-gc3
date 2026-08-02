"""Config flow for the 2GIG GC3 integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from pygc3 import GC3AuthError, GC3Client, GC3ConnectionError, GC3Error

from .const import (
    CONF_BYPASS_NOT_READY,
    CONF_DISARM_PIN,
    CONF_NO_EXIT_DELAY,
    CONF_PAIRING_KEY,
    CONF_PARTITION,
    CONF_POLL_INTERVAL,
    DEFAULT_PARTITION,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PORT,
    DOMAIN,
    MAX_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_API_KEY): str,
        vol.Required(CONF_PAIRING_KEY): str,
        vol.Optional(CONF_DISARM_PIN, default=""): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Optional(CONF_PARTITION, default=DEFAULT_PARTITION): int,
    }
)

STEP_REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_KEY): str,
        vol.Required(CONF_PAIRING_KEY): str,
    }
)


async def _validate(hass: HomeAssistant, data: Mapping[str, Any]) -> str | None:
    """Return an error key, or None if the credentials authenticate.

    This is the `test-before-configure` check: a real authenticated GET /status,
    so a typo'd key fails here rather than after the entry is created.
    """
    client = GC3Client(
        data[CONF_HOST],
        data[CONF_PAIRING_KEY],
        data[CONF_API_KEY],
        port=data.get(CONF_PORT, DEFAULT_PORT),
        partition=data.get(CONF_PARTITION, DEFAULT_PARTITION),
        session=async_get_clientsession(hass, verify_ssl=False),
    )
    try:
        await client.status()
    except GC3AuthError:
        return "invalid_auth"
    except GC3ConnectionError:
        return "cannot_connect"
    except GC3Error:
        return "unknown"
    return None


class GC3ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the GC3 config flow.

    The panel exposes no serial or other identity on /status (see
    INTEGRATION_PLAN.md §1), so the host is the only stable unique_id available.
    """

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a user-initiated setup."""
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_HOST].lower())
            self._abort_if_unique_id_configured()
            if (err := await _validate(self.hass, user_input)) is None:
                return self.async_create_entry(title="2GIG GC3", data=user_input)
            errors["base"] = err
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the host/port/PIN of an existing entry without re-adding it."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_HOST].lower())
            self._abort_if_unique_id_mismatch(reason="wrong_panel")
            if (err := await _validate(self.hass, user_input)) is None:
                return self.async_update_reload_and_abort(entry, data=user_input)
            errors["base"] = err
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input or entry.data
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Credentials stopped working; ask for new ones."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        if user_input is not None:
            merged = {**reauth_entry.data, **user_input}
            if (err := await _validate(self.hass, merged)) is None:
                return self.async_update_reload_and_abort(reauth_entry, data=merged)
            errors["base"] = err
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return GC3OptionsFlow()


class GC3OptionsFlow(OptionsFlow):
    """Polling cadence and the default arm behaviour."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_POLL_INTERVAL,
                        default=options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                    ): vol.All(
                        int, vol.Range(min=MIN_POLL_INTERVAL, max=MAX_POLL_INTERVAL)
                    ),
                    vol.Optional(
                        CONF_NO_EXIT_DELAY,
                        default=options.get(CONF_NO_EXIT_DELAY, False),
                    ): bool,
                    vol.Optional(
                        CONF_BYPASS_NOT_READY,
                        default=options.get(CONF_BYPASS_NOT_READY, False),
                    ): bool,
                }
            ),
        )
