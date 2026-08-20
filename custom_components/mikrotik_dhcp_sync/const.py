"""Constants for MikroTik DHCP Sync."""

from datetime import timedelta

DOMAIN = "mikrotik_dhcp_sync"

CONF_HOST = "host"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_PORT = "port"

DEFAULT_PORT = 8728
UPDATE_INTERVAL = timedelta(seconds=30)

LEASE_ACTIVE_ADDRESS = "active-address"
LEASE_ADDRESS = "address"
LEASE_HOSTNAME = "host-name"
LEASE_MAC = "mac-address"

