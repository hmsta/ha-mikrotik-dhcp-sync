"""Config flow for MikroTik DHCP Sync."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant

from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    DEFAULT_PORT,
    DOMAIN,
)
from .routeros import RouterOSClient, looks_like_auth_error


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for MikroTik DHCP Sync."""

    VERSION = 1

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
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
                }
            ),
            errors=errors,
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
