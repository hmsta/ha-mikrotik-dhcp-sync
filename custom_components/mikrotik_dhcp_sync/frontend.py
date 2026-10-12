"""Serve and register the optional dashboard card independently of lease sync."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import logging
from pathlib import Path
from urllib.parse import urlsplit

_LOGGER = logging.getLogger(__name__)
_DATA_KEY = "mikrotik_dhcp_sync_frontend"
CARD_PATH = Path(__file__).parent / "frontend" / "mikrotik-dhcp-browser-card.js"
CARD_URL = "/mikrotik_dhcp_sync/mikrotik-dhcp-browser-card.js"
_LEGACY_URL = "/local/mikrotik-dhcp-browser-card.js"


@dataclass
class _FrontendState:
    """Keep the HTTP route for the HA process lifetime, across entry reloads."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    registered: bool = False
    url: str | None = None


def _versioned_url() -> str:
    """Invalidate browser caches when the bundled JavaScript changes."""
    digest = hashlib.sha256(CARD_PATH.read_bytes()).hexdigest()[:12]
    return f"{CARD_URL}?v={digest}"


def _is_card_resource(resource: dict) -> bool:
    url = urlsplit(resource.get("url", ""))
    return not url.scheme and not url.netloc and url.path in (CARD_URL, _LEGACY_URL)


async def async_setup_frontend(hass) -> None:
    """Register once, without letting optional UI failures stop DHCP syncing.

    Static serving uses HA's supported HTTP API. Resource collection access is
    internal to Lovelace and deliberately isolated here for compatibility fixes.
    """
    state = hass.data.setdefault(_DATA_KEY, _FrontendState())
    async with state.lock:
        try:
            from homeassistant.components.http import StaticPathConfig
            from homeassistant.components.lovelace.const import LOVELACE_DATA

            if not getattr(hass, "http", None):
                return
            if not state.registered:
                state.url = await hass.async_add_executor_job(_versioned_url)
                await hass.http.async_register_static_paths(
                    [StaticPathConfig(CARD_URL, str(CARD_PATH), False)]
                )
                state.registered = True

            lovelace = hass.data.get(LOVELACE_DATA)
            if lovelace is None:
                _LOGGER.warning("Lovelace unavailable; register card module %s manually", state.url)
                return
            if lovelace.resource_mode != "storage":
                _LOGGER.info(
                    "YAML-managed Lovelace resources: add %s as type module", CARD_URL
                )
                return

            resources = lovelace.resources
            # This public collection method ensures lazy storage has loaded.
            await resources.async_get_info()
            matches = [item for item in resources.async_items() if _is_card_resource(item)]
            data = {"url": state.url, "res_type": "module"}
            if not matches:
                await resources.async_create_item(data)
                return
            first = matches[0]
            if first["url"] != state.url or first.get("type") != "module":
                await resources.async_update_item(first["id"], data)
            # Also migrate the former manual /local resource, avoiding two loads.
            for duplicate in matches[1:]:
                await resources.async_delete_item(duplicate["id"])
        except Exception:
            _LOGGER.exception(
                "Could not automatically register the DHCP Browser card. "
                "DHCP sync will continue; see README for manual setup"
            )
