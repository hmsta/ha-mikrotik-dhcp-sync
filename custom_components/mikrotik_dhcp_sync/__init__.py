"""MikroTik DHCP Sync custom integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import MikrotikDhcpSyncCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up MikroTik DHCP Sync from a config entry."""
    coordinator = MikrotikDhcpSyncCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    coordinator.async_start_polling()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload MikroTik DHCP Sync."""
    coordinator = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if coordinator is not None:
        await coordinator.async_close()
    if not hass.data.get(DOMAIN):
        hass.data.pop(DOMAIN, None)
    return True
