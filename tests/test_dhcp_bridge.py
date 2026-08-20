"""Tests for MikroTik DHCP Sync DHCP cache bridge."""

from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import ip_address
import importlib
import sys
import types

import pytest


@dataclass
class FakeDhcpData:
    """Minimal stand-in for Home Assistant DHCPData."""

    address_data: dict[str, dict[str, str]] = field(default_factory=dict)
    callbacks: set = field(default_factory=set)


@dataclass
class FakeHass:
    """Minimal stand-in for HomeAssistant."""

    data: dict = field(default_factory=dict)


@pytest.fixture
def dhcp_bridge(monkeypatch):
    """Import dhcp_bridge with minimal Home Assistant module stubs."""
    for name in list(sys.modules):
        if name.startswith("homeassistant") or name.startswith(
            "custom_components.mikrotik_dhcp_sync.dhcp_bridge"
        ):
            monkeypatch.delitem(sys.modules, name, raising=False)

    ha = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
    dhcp = types.ModuleType("homeassistant.components.dhcp")
    dhcp_const = types.ModuleType("homeassistant.components.dhcp.const")
    dhcp_const.HOSTNAME = "hostname"
    dhcp_const.IP_ADDRESS = "ip"

    @dataclass
    class DhcpServiceInfo:
        ip: str
        hostname: str
        macaddress: str

    def async_discovered_service_info(hass):
        return [
            DhcpServiceInfo(
                ip=data["ip"],
                hostname=data["hostname"].lower(),
                macaddress=mac,
            )
            for mac, data in hass.data["dhcp"].address_data.items()
        ]

    dhcp.async_discovered_service_info = async_discovered_service_info
    dhcp_helpers = types.ModuleType("homeassistant.components.dhcp.helpers")
    dhcp_helpers.async_get_address_data_internal = lambda hass: hass.data[
        "dhcp"
    ].address_data
    dhcp_models = types.ModuleType("homeassistant.components.dhcp.models")
    dhcp_models.DATA_DHCP = "dhcp"
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    core.callback = lambda func: func
    helpers = types.ModuleType("homeassistant.helpers")
    device_registry = types.ModuleType("homeassistant.helpers.device_registry")

    def format_mac(raw: str) -> str:
        cleaned = raw.replace(":", "").replace("-", "").replace(".", "").lower()
        if len(cleaned) != 12:
            return raw
        return ":".join(cleaned[i : i + 2] for i in range(0, 12, 2))

    device_registry.format_mac = format_mac
    cached_ipaddress = types.ModuleType("cached_ipaddress")

    def cached_ip_addresses(value: str):
        try:
            return ip_address(value)
        except ValueError:
            return None

    cached_ipaddress.cached_ip_addresses = cached_ip_addresses

    monkeypatch.setitem(sys.modules, "homeassistant", ha)
    monkeypatch.setitem(sys.modules, "homeassistant.components", components)
    monkeypatch.setitem(sys.modules, "homeassistant.components.dhcp", dhcp)
    monkeypatch.setitem(sys.modules, "homeassistant.components.dhcp.const", dhcp_const)
    monkeypatch.setitem(
        sys.modules, "homeassistant.components.dhcp.helpers", dhcp_helpers
    )
    monkeypatch.setitem(
        sys.modules, "homeassistant.components.dhcp.models", dhcp_models
    )
    monkeypatch.setitem(sys.modules, "homeassistant.core", core)
    monkeypatch.setitem(sys.modules, "homeassistant.helpers", helpers)
    monkeypatch.setitem(
        sys.modules, "homeassistant.helpers.device_registry", device_registry
    )
    monkeypatch.setitem(sys.modules, "cached_ipaddress", cached_ipaddress)

    return importlib.import_module("custom_components.mikrotik_dhcp_sync.dhcp_bridge")


@pytest.fixture
def hass():
    """Return a fake hass object with DHCP data initialized."""
    return FakeHass(data={"dhcp": FakeDhcpData()})


def import_leases(dhcp_bridge, hass, leases):
    """Import leases through the bridge."""
    return dhcp_bridge.async_import_leases_to_dhcp_cache(hass, leases)


def test_active_valid_mikrotik_lease_is_inserted(dhcp_bridge, hass):
    changed = import_leases(
        dhcp_bridge,
        hass,
        [
            {
                "active-address": "192.168.1.10",
                "address": "192.168.1.10",
                "host-name": "phone",
                "mac-address": "AA:BB:CC:DD:EE:FF",
            }
        ],
    )

    assert changed == {"aabbccddeeff": {"ip": "192.168.1.10", "hostname": "phone"}}
    assert hass.data["dhcp"].address_data == changed


def test_inactive_lease_without_active_address_is_ignored(dhcp_bridge, hass):
    import_leases(
        dhcp_bridge,
        hass,
        [{"address": "192.168.1.20", "mac-address": "AA:BB:CC:DD:EE:FF"}],
    )

    assert hass.data["dhcp"].address_data == {}


@pytest.mark.parametrize(
    "value",
    ["not-an-ip", "169.254.1.2", "127.0.0.1", "0.0.0.0"],
)
def test_invalid_unsupported_ip_is_ignored(dhcp_bridge, hass, value):
    import_leases(
        dhcp_bridge,
        hass,
        [{"active-address": value, "address": value, "mac-address": "aabbccddeeff"}],
    )

    assert hass.data["dhcp"].address_data == {}


def test_malformed_mac_is_ignored(dhcp_bridge, hass):
    import_leases(
        dhcp_bridge,
        hass,
        [
            {
                "active-address": "192.168.1.10",
                "address": "192.168.1.10",
                "mac-address": "not-a-mac",
            }
        ],
    )

    assert hass.data["dhcp"].address_data == {}


def test_mac_key_is_normalized_lowercase_colonless(dhcp_bridge, hass):
    import_leases(
        dhcp_bridge,
        hass,
        [
            {
                "active-address": "192.168.1.10",
                "address": "192.168.1.10",
                "mac-address": "AA-BB-CC-DD-EE-FF",
            }
        ],
    )

    assert list(hass.data["dhcp"].address_data) == ["aabbccddeeff"]


@pytest.mark.parametrize("hostname", ["", "iphone"])
def test_empty_or_shorter_hostname_does_not_overwrite_existing_fuller_hostname(
    dhcp_bridge, hass, hostname
):
    hass.data["dhcp"].address_data["aabbccddeeff"] = {
        "ip": "192.168.1.10",
        "hostname": "iphone-michael",
    }

    changed = import_leases(
        dhcp_bridge,
        hass,
        [
            {
                "active-address": "192.168.1.10",
                "address": "192.168.1.10",
                "host-name": hostname,
                "mac-address": "AA:BB:CC:DD:EE:FF",
            }
        ],
    )

    assert changed == {}
    assert hass.data["dhcp"].address_data["aabbccddeeff"]["hostname"] == "iphone-michael"


def test_different_hostname_updates_record(dhcp_bridge, hass):
    hass.data["dhcp"].address_data["aabbccddeeff"] = {
        "ip": "192.168.1.10",
        "hostname": "old-name",
    }

    import_leases(
        dhcp_bridge,
        hass,
        [
            {
                "active-address": "192.168.1.10",
                "address": "192.168.1.10",
                "host-name": "new-name",
                "mac-address": "AA:BB:CC:DD:EE:FF",
            }
        ],
    )

    assert hass.data["dhcp"].address_data["aabbccddeeff"] == {
        "ip": "192.168.1.10",
        "hostname": "new-name",
    }


def test_ip_change_updates_record(dhcp_bridge, hass):
    hass.data["dhcp"].address_data["aabbccddeeff"] = {
        "ip": "192.168.1.10",
        "hostname": "phone",
    }

    import_leases(
        dhcp_bridge,
        hass,
        [
            {
                "active-address": "192.168.1.11",
                "address": "192.168.1.11",
                "host-name": "phone",
                "mac-address": "AA:BB:CC:DD:EE:FF",
            }
        ],
    )

    assert hass.data["dhcp"].address_data["aabbccddeeff"]["ip"] == "192.168.1.11"


def test_other_dhcp_records_are_never_cleared(dhcp_bridge, hass):
    hass.data["dhcp"].address_data["001122334455"] = {
        "ip": "192.168.1.50",
        "hostname": "other",
    }

    import_leases(dhcp_bridge, hass, [])

    assert hass.data["dhcp"].address_data == {
        "001122334455": {"ip": "192.168.1.50", "hostname": "other"}
    }


def test_empty_hostname_can_be_skipped(dhcp_bridge, hass):
    import_leases(
        dhcp_bridge,
        hass,
        [
            {
                "active-address": "192.168.1.10",
                "address": "192.168.1.10",
                "mac-address": "AA:BB:CC:DD:EE:FF",
            }
        ],
    )

    assert hass.data["dhcp"].address_data == {
        "aabbccddeeff": {"ip": "192.168.1.10", "hostname": ""}
    }

    hass.data["dhcp"].address_data.clear()
    dhcp_bridge.async_import_leases_to_dhcp_cache(
        hass,
        [
            {
                "active-address": "192.168.1.10",
                "address": "192.168.1.10",
                "mac-address": "AA:BB:CC:DD:EE:FF",
            }
        ],
        skip_empty_hostnames=True,
    )

    assert hass.data["dhcp"].address_data == {}


def test_changed_record_is_sent_to_dhcp_subscribers(dhcp_bridge, hass):
    seen = []
    hass.data["dhcp"].callbacks.add(seen.append)

    import_leases(
        dhcp_bridge,
        hass,
        [
            {
                "active-address": "192.168.1.10",
                "address": "192.168.1.10",
                "host-name": "phone",
                "mac-address": "AA:BB:CC:DD:EE:FF",
            }
        ],
    )

    assert seen == [{"aabbccddeeff": {"ip": "192.168.1.10", "hostname": "phone"}}]


def test_public_dhcp_service_info_sees_injected_record(dhcp_bridge, hass):
    import_leases(
        dhcp_bridge,
        hass,
        [
            {
                "active-address": "192.168.1.10",
                "address": "192.168.1.10",
                "host-name": "Phone",
                "mac-address": "AA:BB:CC:DD:EE:FF",
            }
        ],
    )

    from homeassistant.components import dhcp

    devices = dhcp.async_discovered_service_info(hass)

    assert devices[0].ip == "192.168.1.10"
    assert devices[0].hostname == "phone"
    assert devices[0].macaddress == "aabbccddeeff"
