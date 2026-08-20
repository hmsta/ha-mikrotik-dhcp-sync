"""Constants for MikroTik DHCP Sync."""

DOMAIN = "mikrotik_dhcp_sync"

CONF_HOST = "host"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_PORT = "port"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_PORT = 8728
DEFAULT_SCAN_INTERVAL_SECONDS = 30
MIN_SCAN_INTERVAL_SECONDS = 5
MAX_SCAN_INTERVAL_SECONDS = 86400

LEASE_ACTIVE_ADDRESS = "active-address"
LEASE_ADDRESS = "address"
LEASE_HOSTNAME = "host-name"
LEASE_MAC = "mac-address"
