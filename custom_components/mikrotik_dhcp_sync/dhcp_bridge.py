"""Bridge MikroTik DHCP lease data into Home Assistant's DHCP cache.

Home Assistant explicitly marks the DHCP helpers used in this file as internal
and not intended for integrations. This module intentionally contains every
touchpoint with those private internals so future HA Core DHCP changes require
adjustment in one small place instead of throughout the integration.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from cached_ipaddress import cached_ip_addresses
from homeassistant.components.dhcp.const import HOSTNAME, IP_ADDRESS
from homeassistant.components.dhcp.helpers import async_get_address_data_internal
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import format_mac

from .const import (
    LEASE_ACTIVE_ADDRESS,
    LEASE_ADDRESS,
    LEASE_HOSTNAME,
    LEASE_MAC,
)

_LOGGER = logging.getLogger(__name__)
_MAC_RE = re.compile(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}")


@callback
def async_import_leases_to_dhcp_cache(
    hass: HomeAssistant,
    leases: list[dict[str, Any]],
    *,
    skip_empty_hostnames: bool = False,
) -> dict[str, dict[str, str]]:
    """Insert or update active MikroTik leases in HA's DHCP address cache."""
    address_data = async_get_address_data_internal(hass)
    changed: dict[str, dict[str, str]] = {}

    for lease in leases:
        if not lease.get(LEASE_ACTIVE_ADDRESS):
            continue

        ip_address = lease.get(LEASE_ADDRESS) or lease.get(LEASE_ACTIVE_ADDRESS)
        hostname = lease.get(LEASE_HOSTNAME) or ""
        if skip_empty_hostnames and not hostname:
            continue
        raw_mac = lease.get(LEASE_MAC)

        normalized = _normalize_lease(ip_address, raw_mac)
        if normalized is None:
            continue

        mac_key, compressed_ip = normalized
        current_data = address_data.get(mac_key)
        if (
            current_data
            and current_data[IP_ADDRESS] == compressed_ip
            and current_data[HOSTNAME].startswith(hostname)
        ):
            continue

        data = {IP_ADDRESS: compressed_ip, HOSTNAME: hostname}
        address_data[mac_key] = data
        changed[mac_key] = data

    if changed:
        _async_notify_dhcp_subscribers(hass, changed)

    return changed


def _normalize_lease(ip_address: Any, raw_mac: Any) -> tuple[str, str] | None:
    """Validate and normalize a RouterOS lease into HA DHCP cache key/data."""
    if not isinstance(ip_address, str):
        _LOGGER.debug("Ignoring DHCP lease with missing IP address")
        return None

    made_ip_address = cached_ip_addresses(ip_address)
    if made_ip_address is None:
        _LOGGER.debug("Ignoring invalid DHCP lease IP address: %s", ip_address)
        return None
    if (
        made_ip_address.is_link_local
        or made_ip_address.is_loopback
        or made_ip_address.is_unspecified
    ):
        _LOGGER.debug("Ignoring unsupported DHCP lease IP address: %s", ip_address)
        return None

    if not isinstance(raw_mac, str):
        _LOGGER.debug("Ignoring DHCP lease with missing MAC address")
        return None

    try:
        formatted_mac = format_mac(raw_mac)
    except ValueError:
        _LOGGER.debug("Ignoring malformed DHCP lease MAC address: %s", raw_mac)
        return None

    formatted_mac = formatted_mac.lower()
    if _MAC_RE.fullmatch(formatted_mac) is None:
        _LOGGER.debug("Ignoring malformed DHCP lease MAC address: %s", raw_mac)
        return None

    return formatted_mac.replace(":", ""), made_ip_address.compressed


@callback
def _async_notify_dhcp_subscribers(
    hass: HomeAssistant, changed: dict[str, dict[str, str]]
) -> None:
    """Notify DHCP Browser websocket subscribers about changed cache records only."""
    try:
        from homeassistant.components.dhcp.models import DATA_DHCP

        callbacks = hass.data[DATA_DHCP].callbacks
    except (AttributeError, KeyError, ImportError):
        _LOGGER.debug("DHCP subscriber callbacks are unavailable")
        return

    for callback_ in tuple(callbacks):
        try:
            callback_(changed)
        except Exception:
            _LOGGER.exception("DHCP subscriber callback failed")

