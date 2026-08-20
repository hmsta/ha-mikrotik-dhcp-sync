# MikroTik DHCP Sync

Minimal Home Assistant custom integration for syncing active MikroTik RouterOS
DHCP leases into Home Assistant's existing in-memory DHCP discovery cache.

Integration domain: `mikrotik_dhcp_sync`

## What It Does

This integration polls the MikroTik RouterOS DHCP lease table through the native
RouterOS API and injects valid active DHCP client records into Home Assistant's
RAM-based DHCP cache.

Injected records are visible in Home Assistant's DHCP browser:

```text
/config/dhcp
```

They are also available to other integrations through Home Assistant's public
DHCP discovery API:

```python
from homeassistant.components import dhcp

devices = dhcp.async_discovered_service_info(hass)
```

Each injected record is exposed through that API with:

```python
device.ip
device.hostname
device.macaddress
```

## What It Does Not Do

This integration intentionally does not create:

- device tracker entities
- scanner entities
- sensors
- binary sensors
- buttons
- switches
- Home Assistant devices for DHCP clients
- entity registry entries for DHCP clients
- persistent DHCP lease storage
- recorder or history data

DHCP client data exists only in Home Assistant's existing in-memory DHCP cache.
The integration's own config entry, including RouterOS connection settings, is
stored by Home Assistant like any other config entry.

## Requirements

- Home Assistant with custom integrations enabled
- HACS, if installing through HACS
- MikroTik RouterOS API enabled
- A RouterOS user with read/API permission

The integration uses:

```text
TCP 8728
/ip/dhcp-server/lease/getall
```

The RouterOS API port is configurable and defaults to `8728`.

This integration depends on `librouteros==4.1.1`, matching the dependency used
by Home Assistant's official MikroTik integration.

## MikroTik RouterOS Setup

This integration uses the plain RouterOS API service named `api`, not REST and
not `api-ssl`.

Enable the RouterOS API service:

```routeros
/ip service enable api
```

The default API port is `8728`. To keep the default explicit:

```routeros
/ip service set api port=8728
```

For better safety, restrict the API service to your Home Assistant host or
subnet. Replace `192.168.1.10/32` with your Home Assistant IP address:

```routeros
/ip service set api address=192.168.1.10/32
```

Create a dedicated read-only API group and user:

```routeros
/user group add name=homeassistant-dhcp-sync policy=read,api
/user add name=homeassistant-dhcp-sync group=homeassistant-dhcp-sync password=CHANGE_ME
```

If your RouterOS version prompts for the password interactively when adding the
user, you can omit `password=CHANGE_ME` from the `add` command and enter it at
the prompt instead.

This integration only needs:

- `api`: allows login through the native RouterOS API service
- `read`: allows reading `/ip/dhcp-server/lease/getall`

It does not need:

- `write`
- `policy`
- `test`
- `sensitive`
- `reboot`
- `rest-api`
- `winbox`
- SSL certificate setup for `api-ssl`

Home Assistant's official MikroTik integration documents `test` permission
because it can do device-tracker presence checks such as ping/ARP ping. This
integration does not do those checks, so `test` is not required.

## HACS Installation

1. Open HACS in Home Assistant.
2. Go to `Integrations`.
3. Open the menu and choose `Custom repositories`.
4. Add this repository URL.
5. Select category `Integration`.
6. Install `MikroTik DHCP Sync`.
7. Restart Home Assistant.
8. Add the integration from `Settings` -> `Devices & services`.

HACS installs integrations under:

```text
/config/custom_components/
```

## Manual Installation

Copy this directory:

```text
custom_components/mikrotik_dhcp_sync
```

to your Home Assistant configuration directory:

```text
/config/custom_components/mikrotik_dhcp_sync
```

Then restart Home Assistant and add the integration from:

```text
Settings -> Devices & services
```

## Configuration

The config flow asks for:

- `host`
- `username`
- `password`
- `port`

The default port is `8728`.

This integration connects directly to RouterOS and does not depend on Home
Assistant's official `mikrotik` integration.

## Lease Import Rules

Only active leases are imported. A lease is considered active when RouterOS
reports `active-address`.

The IP address is normally read from:

```text
address
```

If `address` is missing, the integration falls back to:

```text
active-address
```

The hostname is read only from:

```text
host-name
```

If `host-name` is missing, the stored hostname is an empty string.

The MAC address is read from:

```text
mac-address
```

MAC addresses are normalized to Home Assistant's historical DHCP cache key
format:

```text
aabbccddeeff
```

Malformed MAC addresses and invalid or unsupported IP addresses are skipped.

## Cache Behavior

The integration only inserts or updates individual MAC records. It never clears
or replaces Home Assistant's full DHCP cache.

If a MikroTik lease disappears, the corresponding Home Assistant DHCP cache
entry is left alone. This matches the discovery-cache nature of Home Assistant's
DHCP data and avoids deleting records that may have come from other discovery
sources.

Existing hostnames are preserved using Home Assistant's current DHCP update
rule. For example, a new empty hostname will not overwrite an existing useful
hostname at the same IP.

## Private Home Assistant DHCP API

This integration intentionally uses Home Assistant's internal DHCP helper:

```python
from homeassistant.components.dhcp.helpers import (
    async_get_address_data_internal,
)
```

Home Assistant explicitly marks this helper as internal and not intended for
integrations. All private DHCP cache access is isolated in:

```text
custom_components/mikrotik_dhcp_sync/dhcp_bridge.py
```

Future Home Assistant Core DHCP changes may require updates to that module.

## Development

Run tests with:

```bash
pytest
```

The test suite focuses on DHCP cache import behavior and the integration's
minimal contract: no entities, no devices for clients, and no persistent DHCP
lease storage.

## Safety Notes

Do not expose the RouterOS API to untrusted networks. Use a dedicated RouterOS
user with the minimum permissions needed to read DHCP leases.

## License

MIT License. See `LICENSE`.
