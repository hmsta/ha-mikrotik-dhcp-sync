"""Config flow for MikroTik DHCP Sync."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback

from .const import (
    CONF_AUTHORITATIVE_SYNC,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SKIP_EMPTY_HOSTNAMES,
    CONF_USERNAME,
    DEFAULT_AUTHORITATIVE_SYNC,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DEFAULT_SKIP_EMPTY_HOSTNAMES,
    DEFAULT_PORT,
    DOMAIN,
    MAX_SCAN_INTERVAL_SECONDS,
    MIN_SCAN_INTERVAL_SECONDS,
)
from .routeros import RouterOSClient, looks_like_auth_error


def _scan_interval_schema() -> vol.All:
    """Return a frontend-serializable scan interval validator."""
    return vol.All(
        vol.Coerce(int),
        vol.Range(min=MIN_SCAN_INTERVAL_SECONDS, max=MAX_SCAN_INTERVAL_SECONDS),
    )


def _setup_schema() -> vol.Schema:
    """Return the setup schema."""
    return vol.Schema(
        {
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_USERNAME): str,
            vol.Required(CONF_PASSWORD): str,
            vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=DEFAULT_SCAN_INTERVAL_SECONDS,
            ): _scan_interval_schema(),
            vol.Optional(
                CONF_SKIP_EMPTY_HOSTNAMES,
                default=DEFAULT_SKIP_EMPTY_HOSTNAMES,
            ): bool,
            vol.Optional(
                CONF_AUTHORITATIVE_SYNC,
                default=DEFAULT_AUTHORITATIVE_SYNC,
            ): bool,
        }
    )


def _options_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Return the options schema."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=defaults.get(
                    CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS
                ),
            ): _scan_interval_schema(),
            vol.Optional(
                CONF_SKIP_EMPTY_HOSTNAMES,
                default=defaults.get(
                    CONF_SKIP_EMPTY_HOSTNAMES, DEFAULT_SKIP_EMPTY_HOSTNAMES
                ),
            ): bool,
            vol.Optional(
                CONF_AUTHORITATIVE_SYNC,
                default=defaults.get(
                    CONF_AUTHORITATIVE_SYNC, DEFAULT_AUTHORITATIVE_SYNC
                ),
            ): bool,
        }
    )


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for MikroTik DHCP Sync."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return OptionsFlowHandler(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(
                f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
            )
            self._abort_if_unique_id_configured()

            try:
                await _async_validate_input(self.hass, user_input)
            except Exception as err:
                errors["base"] = (
                    "invalid_auth" if looks_like_auth_error(err) else "cannot_connect"
                )
            else:
                return self.async_create_entry(
                    title=user_input[CONF_HOST],
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_setup_schema(),
            errors=errors,
        )


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle MikroTik DHCP Sync options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(
                dict(self._config_entry.data) | dict(self._config_entry.options)
            ),
        )


async def _async_validate_input(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Validate RouterOS credentials by reading the DHCP lease table once."""
    client = RouterOSClient(
        data[CONF_HOST],
        data[CONF_USERNAME],
        data[CONF_PASSWORD],
        data[CONF_PORT],
    )
    try:
        await hass.async_add_executor_job(client.fetch_dhcp_leases)
    finally:
        await hass.async_add_executor_job(client.close)
