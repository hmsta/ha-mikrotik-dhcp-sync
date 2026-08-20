"""Config flow for MikroTik DHCP Sync."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback

from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SKIP_EMPTY_HOSTNAMES,
    CONF_USERNAME,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DEFAULT_SKIP_EMPTY_HOSTNAMES,
    DEFAULT_PORT,
    DOMAIN,
    MAX_SCAN_INTERVAL_SECONDS,
    MIN_SCAN_INTERVAL_SECONDS,
)
from .routeros import RouterOSClient, looks_like_auth_error


def _scan_interval_validator(value: Any) -> int:
    """Validate and normalize the scan interval in seconds."""
    return vol.All(
        vol.Coerce(int),
        vol.Range(min=MIN_SCAN_INTERVAL_SECONDS, max=MAX_SCAN_INTERVAL_SECONDS),
    )(value)


def _user_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Return the setup/reconfigure schema."""
    defaults = defaults or {}
    host_field = (
        vol.Required(CONF_HOST, default=defaults[CONF_HOST])
        if CONF_HOST in defaults
        else vol.Required(CONF_HOST)
    )
    username_field = (
        vol.Required(CONF_USERNAME, default=defaults[CONF_USERNAME])
        if CONF_USERNAME in defaults
        else vol.Required(CONF_USERNAME)
    )
    password_field = (
        vol.Required(CONF_PASSWORD, default=defaults[CONF_PASSWORD])
        if CONF_PASSWORD in defaults
        else vol.Required(CONF_PASSWORD)
    )
    return vol.Schema(
        {
            host_field: str,
            username_field: str,
            password_field: str,
            vol.Optional(
                CONF_PORT,
                default=defaults.get(CONF_PORT, DEFAULT_PORT),
            ): int,
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=defaults.get(
                    CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS
                ),
            ): _scan_interval_validator,
            vol.Optional(
                CONF_SKIP_EMPTY_HOSTNAMES,
                default=defaults.get(
                    CONF_SKIP_EMPTY_HOSTNAMES, DEFAULT_SKIP_EMPTY_HOSTNAMES
                ),
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
            ): _scan_interval_validator,
            vol.Optional(
                CONF_SKIP_EMPTY_HOSTNAMES,
                default=defaults.get(
                    CONF_SKIP_EMPTY_HOSTNAMES, DEFAULT_SKIP_EMPTY_HOSTNAMES
                ),
            ): bool,
        }
    )


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for MikroTik DHCP Sync."""

    VERSION = 1
    MINOR_VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return OptionsFlowHandler()

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
            data_schema=_user_schema(),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle reconfiguration of the existing config entry."""
        entry = _get_reconfigure_entry(self)
        if entry is None:
            return self.async_abort(reason="unknown")

        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                await _async_validate_input(self.hass, user_input)
            except Exception as err:
                errors["base"] = (
                    "invalid_auth" if looks_like_auth_error(err) else "cannot_connect"
                )
            else:
                self.hass.config_entries.async_update_entry(
                    entry,
                    data=user_input,
                    options={
                        CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL],
                        CONF_SKIP_EMPTY_HOSTNAMES: user_input[
                            CONF_SKIP_EMPTY_HOSTNAMES
                        ],
                    },
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reconfigure_successful")

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_user_schema(dict(entry.data) | dict(entry.options)),
            errors=errors,
        )


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle MikroTik DHCP Sync options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(),
        )


def _get_reconfigure_entry(
    flow: ConfigFlow,
) -> config_entries.ConfigEntry | None:
    """Return the config entry being reconfigured using older HA-compatible APIs."""
    helper = getattr(flow, "_get_reconfigure_entry", None)
    if helper is not None:
        return helper()

    entry_id = flow.context.get("entry_id")
    if not entry_id:
        return None
    return flow.hass.config_entries.async_get_entry(entry_id)


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
