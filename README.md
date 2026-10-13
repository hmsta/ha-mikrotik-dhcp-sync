# MikroTik DHCP Sync

Minimal Home Assistant custom integration for syncing active MikroTik RouterOS
DHCP leases through Home Assistant's DHCP discovery processor and into its
existing in-memory cache.

Integration domain: `mikrotik_dhcp_sync`

## What It Does

This integration polls the MikroTik RouterOS DHCP lease table through the native
RouterOS API and passes new or changed valid active DHCP client records through
Home Assistant's native DHCP processor. This updates the RAM-based DHCP cache,
notifies browser subscribers, and dispatches DHCP discovery to integrations
matched by Home Assistant's own rules.

For example, an IP change for an already registered ESPHome device reaches its
DHCP handler, which decides whether to update the connection. New ESPHome devices
are not discovered this way: ESPHome's DHCP matcher only covers registered
devices. Optional ESPHome probing can discover new devices as described below.

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

This integration itself does not create:

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

Matching integrations may offer their normal setup flows or update existing
connections in response to DHCP observations.

DHCP cache records and optional discovery bookkeeping remain in memory.
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
4. Add this repository URL:

   ```text
   https://github.com/hmsta/ha-mikrotik-dhcp-sync
   ```

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
- `sync interval in seconds`
- `skip leases without hostnames`
- `delete DHCP cache entries missing from MikroTik`
- `hostname fallback rules`
- `potential ESPHome devices (hostname contains)`

The default port is `8728`.

The default sync interval is `30` seconds. The allowed range is `5` to `86400`
seconds.

`Skip leases without hostnames` is enabled by default. When enabled, MikroTik
leases that do not have `host-name` set are ignored instead of being inserted
with an empty hostname. Disable it if you want active leases without hostnames
to appear in Home Assistant's DHCP cache.

`Delete DHCP cache entries missing from MikroTik` is enabled by default. When
enabled, each poll treats active MikroTik DHCP leases as authoritative and
removes HA DHCP cache entries whose MAC address is not present in the validated
MikroTik result.

`Hostname fallback rules` can assign names to devices whose MikroTik lease has
an empty `host-name`. Use one rule per line:

```text
7C:45:D0=Aircon
7C:45:D0:12:34:56=Bedroom-Aircon
```

Prefix rules append the last four hex characters of the MAC, so
`7C:45:D0:12:34:56` becomes `Aircon-3456`. Full-MAC rules use the configured
hostname exactly, so the same MAC would become `Bedroom-Aircon`.

This integration connects directly to RouterOS and does not depend on Home
Assistant's official `mikrotik` integration.

After installation, these options can be changed from the integration's
`Configure` flow in Home Assistant.

## Optional ESPHome Discovery

Leave **Potential ESPHome devices (hostname contains)** empty to disable this
feature (the default). To enable it, enter words such as:

```text
esp32 sonoff
```

Words may be separated by spaces, commas, semicolons, or newlines. A real RouterOS
DHCP hostname qualifies if it contains **any** word, ignoring case. For example,
`bedroom-Sonoff-switch` qualifies. Hostname fallback rules are not used for this
filter. It applies to matching active leases on the configured router, across
all its DHCP servers; there is no automatic VLAN restriction.

On startup/enabling the option, existing matching leases are considered too.
New MACs, changed active IPs, and changed matching hostnames are reconsidered.
Configured, disabled, ignored, and pending ESPHome devices are checked before
probing. Unchanged successful observations are not repeatedly probed, even if
their setup suggestion is dismissed. Candidate history is in RAM and resets on
reload/restart; configured and ignored entries remain protected by HA's records.

Probing connects directly to the active IP on **TCP 6053** using `aioesphomeapi`.
It requires an ESPHome device-info response or an identifiable encrypted server
hello, not just an open port. A returned MAC must agree with the DHCP lease;
older encrypted firmware that supplies only a name uses the lease MAC. Devices
using a custom API port or without the native API enabled are not supported.

Confirmed candidates are handed internally to HA's ESPHome Zeroconf config flow
and appear as normal discovery suggestions. No multicast announcement or local
DNS record is created. HA handles confirmation and encryption-key entry; this
integration does not provision keys or automatically complete device setup.
The synthetic Zeroconf handoff depends on HA's ESPHome config-flow behavior and
is isolated in `esphome_discovery.py` for future compatibility updates.

Probes run in the background, one per router and at most two across all router
entries, with a ten-second timeout. Failures retry on the first DHCP poll after
1 minute, then 5 minutes, 30 minutes, and subsequently every 6 hours. Missing
leases drop out of the candidate set. Unloading/reconfiguring cancels pending
work. Normal DHCP syncing continues independently.

ESPHome's Python requirements are loaded lazily for the first eligible probe,
using the versions required by your installed HA release. Leaving the field
empty neither installs these requirements nor starts probes. Clearing the field
disables future probing; it does not remove already offered or configured devices.

## Lease Import Rules

Only active leases are imported. A lease is considered active when RouterOS
reports `active-address`.

The IP address is read from the actual active lease:

```text
active-address
```

The configured `address` reservation is not used: it may differ from the IP
currently assigned to the client.

The hostname is read only from:

```text
host-name
```

If `host-name` is missing, the stored hostname is an empty string.

If hostname fallback rules are configured, they are applied before `Skip leases
without hostnames`. This means a matching fallback rule can keep an otherwise
nameless lease from being skipped.

By default, leases with missing or empty `host-name` values are skipped. This
can be disabled in the integration options.

The MAC address is read from:

```text
active-mac-address
```

If RouterOS omits that field, `mac-address` is used instead. Malformed non-text
hostnames are treated as missing rather than stopping the synchronization.

MAC addresses are normalized to Home Assistant's historical DHCP cache key
format:

```text
aabbccddeeff
```

Malformed MAC addresses and invalid or unsupported IP addresses are skipped.

## Cache Behavior

By default, the integration treats active MikroTik DHCP leases as authoritative.
After each successful RouterOS poll, Home Assistant's DHCP cache is updated so
that MAC addresses missing from the validated MikroTik result are removed.

When multiple router entries are loaded, their most recent successful MAC sets
are retained together so one router's cleanup does not delete another router's
active records. Unloading a router releases its retention set; a later
authoritative poll can then clean up records no longer supplied by any router.

Disable `Delete DHCP cache entries missing from MikroTik` if you want the older
additive behavior where MikroTik leases only insert/update HA DHCP cache records
and never delete existing records.

Existing hostnames are preserved using Home Assistant's current DHCP update
rule. For example, a new empty hostname will not overwrite an existing useful
hostname at the same IP.

Unchanged leases do not repeatedly dispatch discovery. A new MAC at an old IP
is processed as a new identity; it does not automatically replace an existing
configured device. Cache cleanup does not remove configured integrations.

## Private Home Assistant DHCP API

This integration intentionally uses Home Assistant's internal DHCP APIs:

```python
from homeassistant.components.dhcp.helpers import (
    async_get_address_data_internal,
)
from homeassistant.components.dhcp import WatcherBase
from homeassistant.components.dhcp.models import DATA_DHCP
```

Home Assistant explicitly marks the cache helper as internal and not intended
for integrations. `WatcherBase` is also internal; it is instantiated only to
process observations, without starting a network watcher. Changed leases are
passed to `async_process_client` before any cache write, so HA's unchanged-record
guard cannot suppress their discovery. All private DHCP access is isolated in:

```text
custom_components/mikrotik_dhcp_sync/dhcp_bridge.py
```

Future Home Assistant Core DHCP changes may require updates to that module.

## Optional DHCP Browser Dashboard Card

The bundled `custom:mikrotik-dhcp-browser-card` displays **Home Assistant's existing
DHCP discovery cache**, including devices discovered by other sources. It makes
no RouterOS requests and creates no entities, history, or persistent database.
An **administrator account** is required; Home Assistant's server authorization
checks remain in force.

Example appearance (illustrative data):

```text
DHCP Browser — 987 devices                       [Refresh]
Search devices [hostname, IP or MAC…]            Rows [50]

Hostname ↑          IP Address          MAC Address
shelly-pump         192.168.10.45        A4:CF:12:34:56:78
esp32-tank          192.168.10.62        E0:72:A1:15:6F:68
brother-printer     192.168.153.199      48:5F:99:36:55:2C

987 matching / 987 total                  Previous 1 / 20 Next
```

### Automatic installation

1. Install/update the integration through HACS or manually as described above.
2. Restart Home Assistant and add the MikroTik DHCP Sync integration if necessary.
3. Reload your browser, then add a Manual dashboard card using the YAML below
   (or choose **DHCP Browser** in the custom card picker).

For the normal **UI-managed resource** configuration, the integration serves and
registers the bundled JavaScript automatically. There is no file to copy and no
resource to register manually. Existing installations using the previous
`/local/mikrotik-dhcp-browser-card.js` instructions are migrated automatically;
the old copied file can then be deleted.

Registration is shared across integration entries and remains stable across
reloads/restarts. When the bundled file changes, its content hash updates the
resource URL to invalidate browser caches. Reload your browser after an upgrade.
The HTTP route remains available for the Home Assistant process lifetime, and
unloading a RouterOS entry does not remove a resource used by dashboards.
When permanently uninstalling the integration, remove its cards and its resource
under **Settings → Dashboards → Resources**, then restart Home Assistant.

Static serving uses Home Assistant's
[HTTP static-path API](https://developers.home-assistant.io/blog/2024/06/18/async_register_static_paths/).
Automatic registration uses Lovelace's internal
[resource collection](https://github.com/home-assistant/core/blob/2026.10.0/homeassistant/components/lovelace/resources.py),
isolated in `frontend.py`. A future Core change could require an update to that
module. Registration errors are logged without stopping DHCP lease sync.

### YAML-managed resources and manual fallback

If your resources are managed in YAML, merge this into the existing `lovelace`
section of `configuration.yaml`, then reload resources or restart Home Assistant:

```yaml
lovelace:
  resource_mode: yaml
  resources:
    - url: /mikrotik_dhcp_sync/mikrotik-dhcp-browser-card.js
      type: module
```

No file copy is needed. The integration does not rewrite your YAML. If automatic
registration fails in UI mode, add the same URL as a **JavaScript module** under
**Settings → Dashboards → Resources** (enable Advanced Mode in your profile if
needed). See Home Assistant's
[resource instructions](https://developers.home-assistant.io/docs/frontend/custom-ui/registering-resources/).

For standalone use **without configuring this integration**, copy
`custom_components/mikrotik_dhcp_sync/frontend/mikrotik-dhcp-browser-card.js` to
`/config/www/mikrotik-dhcp-browser-card.js` and register
`/local/mikrotik-dhcp-browser-card.js?v=1` as a JavaScript module instead. Restart
Home Assistant if you just created `www`. Ensure HA's `dhcp` integration is loaded
(normally through `default_config:`). On standalone upgrades, copy the updated
file, increment the version in the existing resource, and reload your browser.

### Dashboard configuration

```yaml
type: custom:mikrotik-dhcp-browser-card
title: DHCP Browser
page_size: 50
```

```yaml
type: custom:mikrotik-dhcp-browser-card
title: Network Devices
page_size: 50
subnet: 192.168.40.0/22
```

| Option | Default | Description |
| --- | --- | --- |
| `type` | Required | `custom:mikrotik-dhcp-browser-card` |
| `title` | `DHCP Browser` | Card heading |
| `page_size` | `50` | Initial rows per page: `25`, `50`, or `100` |
| `subnet` | None | Optional IPv4 CIDR; `/0` through `/32` supported |

Search matches hostname, IP, or displayed MAC without case sensitivity. Click a
column heading to toggle ascending/descending sorting; IPs sort numerically.
Missing hostnames/IPs show `—`; malformed MAC records are skipped. IPv6 addresses
are displayed and sorted too, but CIDR filtering currently supports IPv4 only.
The page selector is local to the card. Search, sorting, filtering, and pagination
happen in the browser before rendering at most 100 device rows. Multiple cards
on the same HA connection share one subscription. Unrelated entity state updates
do not rebuild the table. Theme colors follow Home Assistant; narrow screens can
scroll the table horizontally.

The card defaults to the full width of its section, with compact table rows and
selectable text for copying hostnames, IPs, and MACs. If an existing card has an
explicit narrower layout saved, enable **Full width card** in its Layout tab or
set `grid_options: {columns: full}` in its YAML to override that saved setting.

### Cache updates, removals, and compatibility

Protocol compatibility was checked against Core **2026.8.0** and **2026.10.0**
source; these are reference versions, not a claim that every earlier version is
unsupported. The browser must support native JS modules, Web Components, and
BigInt. The card shows an error if DHCP's command is unavailable.

Core's [DHCP WebSocket implementation](https://github.com/home-assistant/core/blob/2026.10.0/homeassistant/components/dhcp/websocket_api.py)
(also [2026.8.0](https://github.com/home-assistant/core/blob/2026.8.0/homeassistant/components/dhcp/websocket_api.py))
sends `{add: [...]}`: a full snapshot on subscription, then incremental updates.
Records are merged by MAC. **There is no removal notification**, including when
this integration deletes stale cache entries; an empty `add` is not a reset.
Removed devices can remain displayed until **Refresh**, a connection reconnect,
or leaving/reopening the dashboard after its cards disconnect. Refresh replaces
the shared subscription and takes a new snapshot. There is no periodic full-cache
polling, and refreshing never triggers a RouterOS poll.

The [HA WebSocket client](https://github.com/home-assistant/home-assistant-js-websocket/blob/master/lib/connection.ts)
automatically restores subscriptions after reconnect. The card treats the next
event as a replacement snapshot and releases its subscription when its last
instance disconnects, including a late subscription acknowledgement. Connection
failures are shown in the card; Refresh retries a failed initial subscription.
Cache contents represent discovery history, not proof that a device is online.

## Development

Run tests with:

```bash
pytest
node --test tests/frontend/card.test.mjs
```

The test suite focuses on DHCP cache import behavior and the integration's
minimal contract: no entities, no devices for clients, and no persistent DHCP
lease storage.

Install `pytest` and `voluptuous` for the Python tests. Installing the
`aioesphomeapi` version used by HA also enables the optional localhost protocol
tests for plaintext and encrypted discovery (validated with `46.6.0`). Without
that library those two tests are skipped. They do not contact real devices.

The frontend tests use Node.js 20+ and its built-in test runner, with no npm
dependencies or build step. An optional real-browser smoke test covers rendering
and interaction with 2,000 mock records, subscription sharing, mobile light/dark
themes, and cleanup:

```bash
npm install --no-save --package-lock=false playwright
npx playwright install chromium
npm run test:browser
```

Set `PLAYWRIGHT_CHANNEL=msedge` to use installed Edge instead of downloaded
Chromium, or `PLAYWRIGHT_MODULE` to an existing Playwright module entry point.
`CARD_SCREENSHOT_DIR` optionally saves mobile light/dark screenshots. These are
mock protocol/browser tests; an authenticated live Home Assistant instance is
still recommended for final deployment validation.

## Safety Notes

Do not expose the RouterOS API to untrusted networks. Use a dedicated RouterOS
user with the minimum permissions needed to read DHCP leases.

## License

MIT License. See `LICENSE`.
