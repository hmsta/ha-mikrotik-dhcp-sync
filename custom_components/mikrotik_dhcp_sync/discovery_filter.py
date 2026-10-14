"""Shared subnet allowlist for DHCP discovery and ESPHome probes."""

from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network
import re

DiscoveryNetworks = tuple[IPv4Network | IPv6Network, ...]


def parse_discovery_subnets(value: str) -> DiscoveryNetworks:
    """Parse CIDRs; blank allows all, malformed nonblank input is rejected."""
    if not value.strip():
        return ()
    tokens = [token for token in re.split(r"[\s,;]+", value.strip()) if token]
    if not tokens or any("/" not in token for token in tokens):
        raise ValueError("Enter subnets in CIDR notation")
    return tuple(dict.fromkeys(ip_network(token, strict=False) for token in tokens))


def discovery_allowed(address: str, networks: DiscoveryNetworks) -> bool:
    """Match an active address against any configured subnet."""
    if not networks:
        return True
    ip = ip_address(address)
    return any(ip in network for network in networks)
