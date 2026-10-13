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
from homeassistant.components.dhcp import WatcherBase
from homeassistant.components.dhcp.const import HOSTNAME, IP_ADDRESS
from homeassistant.components.dhcp.helpers import async_get_address_data_internal
from homeassistant.components.dhcp.models import DATA_DHCP
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import format_mac

from .const import (
    LEASE_ACTIVE_ADDRESS,
    LEASE_ACTIVE_MAC,
    LEASE_HOSTNAME,
    LEASE_MAC,
)

_LOGGER = logging.getLogger(__name__)
_MAC_RE = re.compile(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}")
_SOURCES_KEY = "mikrotik_dhcp_sync_sources"


@callback
def async_import_leases_to_dhcp_cache(
    hass: HomeAssistant,
    leases: list[dict[str, Any]],
    *,
    skip_empty_hostnames: bool = False,
    authoritative_sync: bool = False,
    hostname_fallback_rules: dict[str, str] | None = None,
    source_id: str | None = None,
) -> dict[str, dict[str, str]]:
    """Process active MikroTik leases through HA's DHCP discovery pipeline."""
    address_data = async_get_address_data_internal(hass)
    # Use the native processor without starting another network watcher. It owns
    # cache writes, subscriber notifications, matching, and discovery dispatch.
    processor = WatcherBase(hass, hass.data[DATA_DHCP])
    changed: dict[str, dict[str, str]] = {}
    desired: dict[str, dict[str, str]] = {}

    for lease in leases:
        normalized = normalize_active_lease(lease)
        if normalized is None:
            continue

        hostname = lease.get(LEASE_HOSTNAME)
        if not isinstance(hostname, str):
            hostname = ""
        mac_key, compressed_ip = normalized
        if not hostname and hostname_fallback_rules:
            hostname = _fallback_hostname(mac_key, hostname_fallback_rules)
        if skip_empty_hostnames and not hostname:
            continue
        desired[mac_key] = {IP_ADDRESS: compressed_ip, HOSTNAME: hostname}

    retained_macs = set(desired)
    if source_id is not None:
        sources = hass.data.setdefault(_SOURCES_KEY, {})
        sources[source_id] = set(desired)
        retained_macs.update(mac for macs in sources.values() for mac in macs)
    if authoritative_sync:
        for mac_key in tuple(address_data):
            if mac_key not in retained_macs:
                del address_data[mac_key]

    for mac_key, desired_data in desired.items():
        compressed_ip = desired_data[IP_ADDRESS]
        hostname = desired_data[HOSTNAME]
        current_data = address_data.get(mac_key)
        if (
            current_data
            and current_data[IP_ADDRESS] == compressed_ip
            and current_data[HOSTNAME].startswith(hostname)
        ):
            continue

        # Do not prepopulate the cache: HA would see an unchanged observation
        # and return before notifying the matching integrations.
        processor.async_process_client(compressed_ip, hostname, mac_key)
        changed[mac_key] = address_data[mac_key]

    return changed


@callback
def async_forget_source(hass: HomeAssistant, source_id: str) -> None:
    """Release a router's retention set without deleting shared cache records."""
    if (sources := hass.data.get(_SOURCES_KEY)) is not None:
        sources.pop(source_id, None)
        if not sources:
            hass.data.pop(_SOURCES_KEY, None)


def normalize_active_lease(lease: dict[str, Any]) -> tuple[str, str] | None:
    """Use the client's active identity, not a possibly different reservation."""
    return _normalize_lease(
        lease.get(LEASE_ACTIVE_ADDRESS),
        lease.get(LEASE_ACTIVE_MAC) or lease.get(LEASE_MAC),
    )


def _fallback_hostname(mac_key: str, rules: dict[str, str]) -> str:
    """Return fallback hostname for a normalized MAC key."""
    for prefix, hostname in rules.items():
        if not mac_key.startswith(prefix):
            continue
        if len(prefix) == 12:
            return hostname
        return f"{hostname}-{mac_key[-4:]}"
    return ""


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
        or made_ip_address.is_multicast
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
