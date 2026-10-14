"""Subnet parsing and boundary behavior without a Home Assistant runtime."""

import pytest

from test_dhcp_bridge import dhcp_bridge  # noqa: F401


@pytest.mark.parametrize(("address", "allowed"), [
    ("192.168.10.0", True), ("192.168.10.255", True), ("192.168.11.0", False),
    ("192.168.30.5", True), ("192.168.30.6", False), ("10.0.0.1", True),
    ("fd00::1", True), ("fd00:0:0:1::1", False),
])
def test_multiple_cidrs_and_boundaries(dhcp_bridge, address, allowed):
    from custom_components.mikrotik_dhcp_sync.discovery_filter import (
        discovery_allowed, parse_discovery_subnets,
    )

    networks = parse_discovery_subnets("192.168.10.5/24,192.168.30.5/32;\nfd00::/64 10.0.0.1/32")
    assert discovery_allowed(address, networks) is allowed


def test_blank_allows_all_and_zero_prefix_is_version_specific(dhcp_bridge):
    from custom_components.mikrotik_dhcp_sync.discovery_filter import (
        discovery_allowed, parse_discovery_subnets,
    )

    assert discovery_allowed("192.168.99.20", parse_discovery_subnets(" \n"))
    assert discovery_allowed("fd00::1", parse_discovery_subnets(""))
    assert discovery_allowed("192.168.99.20", parse_discovery_subnets("0.0.0.0/0"))
    assert not discovery_allowed("fd00::1", parse_discovery_subnets("0.0.0.0/0"))
