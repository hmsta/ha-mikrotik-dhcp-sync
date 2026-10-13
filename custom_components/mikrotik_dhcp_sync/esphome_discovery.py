"""Optional hostname-filtered ESPHome discovery from RouterOS leases.

The synthetic Zeroconf handoff is a compatibility adapter for HA's ESPHome
config flow, not a network announcement. No keys or probe history are persisted.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from ipaddress import ip_address
import logging
import re
from time import monotonic
from typing import Any

from homeassistant.core import HomeAssistant, callback

from .const import LEASE_HOSTNAME
from .dhcp_bridge import normalize_active_lease

_LOGGER = logging.getLogger(__name__)
_RUNTIME_KEY = "mikrotik_dhcp_sync_esphome_probes"
_PORT = 6053
_PROBE_TIMEOUT = 10
_RETRY_DELAYS = (60, 300, 1800, 21600)
_SERVICE = "_esphomelib._tcp.local."
# Deliberately incorrect key to obtain the encrypted server hello, as HA does.
# This is NOT the zero provisioning key; the probe never provisions a device.
_PROBE_KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="


@dataclass(frozen=True)
class Candidate:
    mac: str
    host: str
    hostname: str


@dataclass
class ProbeState:
    candidate: Candidate
    attempts: int = 0
    next_attempt: float = 0


@dataclass(frozen=True)
class DeviceInfo:
    name: str
    friendly_name: str
    encrypted: bool


@dataclass
class ProbeRuntime:
    """Bound concurrent probes and deduplicate across RouterOS config entries."""

    semaphore: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(2))
    in_flight: set[str] = field(default_factory=set)


def _mac_key(value: str | None) -> str:
    normalized = re.sub(r"[:.\-]", "", value or "").lower()
    return normalized if re.fullmatch(r"[0-9a-f]{12}", normalized) else ""


class ESPHomeDiscovery:
    """Maintain a per-router candidate set independently of HA's DHCP cache."""

    def __init__(self, hass: HomeAssistant, hostname_filter: str) -> None:
        self.hass = hass
        self._tokens = tuple(filter(None, re.split(r"[\s,;]+", hostname_filter.casefold())))
        self._states: dict[str, ProbeState] = {}
        self._task: asyncio.Task | None = None
        self._closed = False
        self._api: Any = None

    @callback
    def async_update_leases(self, leases: list[dict[str, Any]]) -> None:
        """Refresh candidates; an empty filter performs no discovery work."""
        if self._closed or not self._tokens:
            return
        candidates = {}
        for lease in leases:
            hostname = lease.get(LEASE_HOSTNAME)
            if not isinstance(hostname, str) or not any(
                token in hostname.casefold() for token in self._tokens
            ):
                continue
            # Probe the actual active address, never an inactive reservation.
            normalized = normalize_active_lease(lease)
            if normalized is None:
                continue
            mac, host = normalized
            address = ip_address(host)
            if address.is_multicast or address.is_reserved:
                continue
            candidates[mac] = Candidate(mac, host, hostname)

        self._states = {
            mac: old if (old := self._states.get(mac)) and old.candidate == candidate
            else ProbeState(candidate)
            for mac, candidate in candidates.items()
        }
        if self._task is None or self._task.done():
            if any(state.next_attempt <= monotonic() for state in self._states.values()):
                self._task = self.hass.async_create_background_task(
                    self._async_process(), "MikroTik ESPHome discovery"
                )

    def _already_known(self, candidate: Candidate) -> bool:
        """Include configured, disabled, ignored, and pending ESPHome entries."""
        for entry in self.hass.config_entries.async_entries("esphome"):
            if _mac_key(entry.unique_id) == candidate.mac:
                return True
            if not entry.unique_id and entry.data.get("host") == candidate.host:
                return True
        return any(
            _mac_key(flow.get("context", {}).get("unique_id")) == candidate.mac
            for flow in self.hass.config_entries.flow.async_progress_by_handler("esphome")
        )

    async def _async_load_api(self) -> Any:
        if self._api is None:
            from homeassistant.helpers.importlib import async_import_module
            from homeassistant.requirements import async_get_integration_with_requirements

            # Resolve the version required by the installed HA release. Do not
            # pin a competing aioesphomeapi version in our manifest.
            await async_get_integration_with_requirements(self.hass, "esphome")
            self._api = await async_import_module(self.hass, "aioesphomeapi")
        return self._api

    async def _async_process(self) -> None:
        runtime = self.hass.data.setdefault(_RUNTIME_KEY, ProbeRuntime())
        for state in list(self._states.values()):
            candidate = state.candidate
            if state.next_attempt > monotonic() or candidate.mac in runtime.in_flight:
                continue
            # Mark before awaiting, including dependency loading and flow setup.
            runtime.in_flight.add(candidate.mac)
            try:
                async with runtime.semaphore:
                    if self._states.get(candidate.mac) is not state:
                        continue
                    if self._already_known(candidate):
                        # Recheck cheaply on later polls if the entry is removed.
                        continue
                    api = await self._async_load_api()
                    if self._states.get(candidate.mac) is not state or self._already_known(candidate):
                        continue
                    async with asyncio.timeout(_PROBE_TIMEOUT):
                        info = await _async_probe(api, candidate)
                    if self._states.get(candidate.mac) is not state:
                        continue  # Lease disappeared or changed during the probe.
                    if info is not None:
                        if not self._already_known(candidate):
                            result = await self._async_announce(candidate, info)
                            if result.get("type") == "abort" and result.get("reason") not in (
                                "already_configured", "already_configured_detailed",
                                "already_configured_updates", "already_in_progress",
                            ):
                                raise RuntimeError(f"ESPHome discovery aborted: {result.get('reason')}")
                        # No more probes for this observation, even if dismissed.
                        state.next_attempt = float("inf")
                        continue
            except Exception:
                # Optional discovery must not break lease syncing. Also retry
                # failed dependency loads / handoffs instead of losing candidates.
                _LOGGER.debug("ESPHome discovery failed for %s", candidate.host, exc_info=True)
            finally:
                runtime.in_flight.discard(candidate.mac)
            delay = _RETRY_DELAYS[min(state.attempts, len(_RETRY_DELAYS) - 1)]
            state.attempts += 1
            state.next_attempt = monotonic() + delay

    async def _async_announce(self, candidate: Candidate, info: DeviceInfo) -> dict:
        """Offer HA's native ESPHome confirmation without publishing multicast."""
        from homeassistant.config_entries import SOURCE_ZEROCONF
        from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

        address = ip_address(candidate.host)
        properties = {"mac": candidate.mac, "friendly_name": info.friendly_name or info.name}
        if info.encrypted:
            properties["api_encryption"] = "Noise_NNpsk0_25519_ChaChaPoly_SHA256"
        discovery = ZeroconfServiceInfo(
            ip_address=address, ip_addresses=[address], port=_PORT,
            hostname=f"{info.name}.local.", type=_SERVICE,
            name=f"{info.name}.{_SERVICE}", properties=properties,
        )
        return await self.hass.config_entries.flow.async_init(
            "esphome", context={"source": SOURCE_ZEROCONF}, data=discovery
        )

    async def async_close(self) -> None:
        """Cancel background work on unload or when options are reloaded."""
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._states.clear()


async def _async_probe(api: Any, candidate: Candidate) -> DeviceInfo | None:
    """Require native device info or an identifiable encrypted server hello."""
    for encrypted in (False, True):
        client = api.APIClient(
            candidate.host, _PORT, password="",
            noise_psk=_PROBE_KEY if encrypted else None,
        )
        try:
            await client.connect(login=False)
            info = await client.device_info()
            if not info.name or _mac_key(info.mac_address) != candidate.mac:
                return None
            return DeviceInfo(info.name, info.friendly_name, encrypted)
        except api.RequiresEncryptionAPIError:
            if encrypted:
                return None
        except api.InvalidEncryptionKeyAPIError as err:
            # Older firmware may supply a name but no MAC in the Noise hello.
            # In that case use the DHCP MAC; never accept a contradictory MAC.
            name = getattr(err, "received_name", None)
            mac = getattr(err, "received_mac", None)
            if encrypted and name and (not mac or _mac_key(mac) == candidate.mac):
                return DeviceInfo(name, name, True)
            return None
        except api.APIConnectionError:
            return None
        finally:
            await client.disconnect(force=True)
    return None
