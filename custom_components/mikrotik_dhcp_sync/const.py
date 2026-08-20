"""Constants for MikroTik DHCP Sync."""

from datetime import timedelta

DOMAIN = "mikrotik_dhcp_sync"

CONF_HOST = "host"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_PORT = "port"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_SKIP_EMPTY_HOSTNAMES = "skip_empty_hostnames"

DEFAULT_PORT = 8728
DEFAULT_SCAN_INTERVAL_SECONDS = 30
MIN_SCAN_INTERVAL_SECONDS = 5
MAX_SCAN_INTERVAL_SECONDS = 86400
DEFAULT_SKIP_EMPTY_HOSTNAMES = True
UPDATE_INTERVAL = timedelta(seconds=DEFAULT_SCAN_INTERVAL_SECONDS)

LEASE_ACTIVE_ADDRESS = "active-address"
LEASE_ADDRESS = "address"
LEASE_HOSTNAME = "host-name"
LEASE_MAC = "mac-address"

