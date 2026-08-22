"""Polling coordinator for MikroTik DHCP Sync."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_AUTHORITATIVE_SYNC,
    CONF_HOST,
    CONF_HOSTNAME_FALLBACK_RULES,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SKIP_EMPTY_HOSTNAMES,
    CONF_USERNAME,
    DEFAULT_AUTHORITATIVE_SYNC,
    DEFAULT_HOSTNAME_FALLBACK_RULES,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DEFAULT_SKIP_EMPTY_HOSTNAMES,
    DOMAIN,
)
from .dhcp_bridge import async_import_leases_to_dhcp_cache
from .routeros import RouterOSClient, looks_like_auth_error

_LOGGER = logging.getLogger(__name__)


class MikrotikDhcpSyncCoordinator(DataUpdateCoordinator[dict[str, dict[str, str]]]):
    """Poll RouterOS DHCP leases and sync HA's DHCP cache."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=self._update_interval_from_entry(entry),
        )
        self._client = RouterOSClient(
            entry.data[CONF_HOST],
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
            entry.data[CONF_PORT],
        )
        self._unsub_poll: CALLBACK_TYPE | None = None
        self._skip_empty_hostnames = entry.options.get(
            CONF_SKIP_EMPTY_HOSTNAMES,
            entry.data.get(CONF_SKIP_EMPTY_HOSTNAMES, DEFAULT_SKIP_EMPTY_HOSTNAMES),
        )
        self._authoritative_sync = entry.options.get(
            CONF_AUTHORITATIVE_SYNC,
            entry.data.get(CONF_AUTHORITATIVE_SYNC, DEFAULT_AUTHORITATIVE_SYNC),
        )
        fallback_rules_text = entry.options.get(
            CONF_HOSTNAME_FALLBACK_RULES,
            entry.data.get(
                CONF_HOSTNAME_FALLBACK_RULES, DEFAULT_HOSTNAME_FALLBACK_RULES
            ),
        )
        self._hostname_fallback_rules = _parse_hostname_fallback_rules(
            fallback_rules_text
        )

    @staticmethod
    def _update_interval_from_entry(entry: ConfigEntry) -> timedelta:
        """Return the configured polling interval."""
        seconds = entry.options.get(
            CONF_SCAN_INTERVAL,
            entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS),
        )
        return timedelta(seconds=seconds)

    @callback
    def async_start_polling(self) -> None:
        """Start periodic polling without relying on entity listeners."""
        if self._unsub_poll is not None:
            return

        async def _async_refresh(_: datetime) -> None:
            await self.async_request_refresh()

        self._unsub_poll = async_track_time_interval(
            self.hass,
            _async_refresh,
            self.update_interval,
            name="MikroTik DHCP Sync polling",
        )

    async def _async_update_data(self) -> dict[str, dict[str, str]]:
        """Fetch RouterOS leases and import active clients into HA's DHCP cache."""
        try:
            leases = await self.hass.async_add_executor_job(
                self._client.fetch_dhcp_leases
            )
        except Exception as err:
            if looks_like_auth_error(err):
                _LOGGER.error(
                    "Authentication failed while connecting to MikroTik RouterOS API at %s",
                    self.config_entry.data[CONF_HOST],
                )
            else:
                _LOGGER.error(
                    "Failed to fetch MikroTik DHCP leases from %s: %s",
                    self.config_entry.data[CONF_HOST],
                    err,
                )
            raise UpdateFailed("Failed to fetch MikroTik DHCP leases") from err

        return async_import_leases_to_dhcp_cache(
            self.hass,
            leases,
            skip_empty_hostnames=self._skip_empty_hostnames,
            authoritative_sync=self._authoritative_sync,
            hostname_fallback_rules=self._hostname_fallback_rules,
        )

    async def async_close(self) -> None:
        """Close the RouterOS API connection."""
        if self._unsub_poll is not None:
            self._unsub_poll()
            self._unsub_poll = None
        await self.hass.async_add_executor_job(self._client.close)


def _parse_hostname_fallback_rules(rules_text: str) -> dict[str, str]:
    """Parse MAC-prefix hostname fallback rules."""
    rules: dict[str, str] = {}
    for line_no, raw_line in enumerate(rules_text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            _LOGGER.warning("Ignoring hostname fallback rule %s without '='", line_no)
            continue
        raw_prefix, raw_hostname = line.split("=", 1)
        prefix = _normalize_mac_prefix(raw_prefix)
        hostname = raw_hostname.strip()
        if prefix is None:
            _LOGGER.warning(
                "Ignoring hostname fallback rule %s with invalid MAC prefix",
                line_no,
            )
            continue
        if not hostname:
            _LOGGER.warning(
                "Ignoring hostname fallback rule %s with empty hostname",
                line_no,
            )
            continue
        rules[prefix] = hostname
    return dict(sorted(rules.items(), key=lambda item: len(item[0]), reverse=True))


def _normalize_mac_prefix(raw_prefix: str) -> str | None:
    """Normalize a MAC prefix to lowercase colonless hex."""
    prefix = re.sub(r"[:.\-\s]", "", raw_prefix.strip())
    if (
        not prefix
        or len(prefix) > 12
        or len(prefix) % 2 != 0
        or re.fullmatch(r"[0-9a-fA-F]+", prefix) is None
    ):
        return None
    return prefix.lower()
