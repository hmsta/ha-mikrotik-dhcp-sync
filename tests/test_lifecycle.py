"""Regression checks for failed setup and unloading during executor work."""

import asyncio
import importlib
from pathlib import Path
import sys
from threading import Event
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from test_dhcp_bridge import dhcp_bridge  # noqa: F401


@pytest.fixture
def coordinator(dhcp_bridge, monkeypatch):
    core = sys.modules["homeassistant.core"]
    core.CALLBACK_TYPE = object
    entries = ModuleType("homeassistant.config_entries")
    entries.ConfigEntry = object
    event = ModuleType("homeassistant.helpers.event")
    event.async_track_time_interval = Mock(return_value=Mock())
    update = ModuleType("homeassistant.helpers.update_coordinator")

    class BaseCoordinator:
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, hass, logger, *, config_entry, name, update_interval):
            self.hass = hass
            self.config_entry = config_entry
            self.update_interval = update_interval
            self.shutdown = False

        async def async_shutdown(self):
            self.shutdown = True

    update.DataUpdateCoordinator = BaseCoordinator
    update.UpdateFailed = type("UpdateFailed", (Exception,), {})
    for module in (entries, event, update):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    name = "custom_components.mikrotik_dhcp_sync.coordinator"
    monkeypatch.delitem(sys.modules, name, raising=False)
    return importlib.import_module(name)


def make_entry(**options):
    return SimpleNamespace(
        entry_id="router", options=options, pref_disable_polling=False,
        data={"host": "router", "username": "user", "password": "secret", "port": 8728},
    )


def test_unload_waits_for_socket_operation_and_discards_late_result(coordinator, monkeypatch):
    async def run():
        started, release = Event(), Event()
        api = SimpleNamespace(closed=False)

        class BlockingAPI:
            def __call__(self, command):
                started.set()
                assert release.wait(3)
                assert not api.closed, "Socket was closed during the lease read"
                return [{"active-address": "192.168.1.10"}]

            def close(self):
                api.closed = True

        hass = SimpleNamespace(data={}, async_add_executor_job=asyncio.to_thread)
        instance = coordinator.MikrotikDhcpSyncCoordinator(hass, make_entry())
        instance._client._api = BlockingAPI()
        publish = Mock()
        monkeypatch.setattr(coordinator, "async_import_leases_to_dhcp_cache", publish)
        instance._esphome_discovery.async_update_leases = Mock()
        refresh = asyncio.create_task(instance._async_update_data())
        close = None
        try:
            assert await asyncio.to_thread(started.wait, 2)
            close = asyncio.create_task(instance.async_close())
            await asyncio.sleep(0.02)
            assert not close.done() and not api.closed
        finally:
            release.set()
            await refresh
            if close:
                await close
        assert api.closed and instance.shutdown
        publish.assert_not_called()
        instance._esphome_discovery.async_update_leases.assert_not_called()
        assert await instance._async_update_data() == {}
    asyncio.run(run())


def test_cancelled_poll_cannot_race_with_socket_close(coordinator):
    async def run():
        started, release = Event(), Event()
        closed = []

        class BlockingAPI:
            def __call__(self, command):
                started.set()
                assert release.wait(3)
                assert not closed
                return []

            def close(self):
                closed.append(True)

        hass = SimpleNamespace(data={}, async_add_executor_job=asyncio.to_thread)
        instance = coordinator.MikrotikDhcpSyncCoordinator(hass, make_entry())
        instance._client._api = BlockingAPI()
        refresh = asyncio.create_task(instance._async_update_data())
        close = None
        try:
            assert await asyncio.to_thread(started.wait, 2)
            refresh.cancel()
            await asyncio.gather(refresh, return_exceptions=True)
            close = asyncio.create_task(instance.async_close())
            await asyncio.sleep(0.02)
            assert not closed
        finally:
            release.set()
            if close:
                await close
        assert closed == [True]
    asyncio.run(run())


def test_disabled_polling_does_not_register_timer(coordinator):
    entry = make_entry()
    entry.pref_disable_polling = True
    instance = coordinator.MikrotikDhcpSyncCoordinator(SimpleNamespace(), entry)
    instance.async_start_polling()
    coordinator.async_track_time_interval.assert_not_called()


@pytest.mark.parametrize("error", [RuntimeError("first refresh failed"), asyncio.CancelledError()])
def test_failed_or_cancelled_setup_closes_coordinator(coordinator, monkeypatch, error):
    async def run():
        fake = SimpleNamespace(
            async_config_entry_first_refresh=AsyncMock(side_effect=error),
            async_close=AsyncMock(), async_start_polling=Mock(),
        )
        monkeypatch.setattr(coordinator, "MikrotikDhcpSyncCoordinator", lambda *args: fake)
        frontend = ModuleType("custom_components.mikrotik_dhcp_sync.frontend")
        frontend.async_setup_frontend = AsyncMock()
        monkeypatch.setitem(sys.modules, frontend.__name__, frontend)
        path = Path(__file__).parents[1] / "custom_components/mikrotik_dhcp_sync/__init__.py"
        namespace = {"__package__": "custom_components.mikrotik_dhcp_sync"}
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)
        hass = SimpleNamespace(data={})
        with pytest.raises(type(error)):
            await namespace["async_setup_entry"](hass, make_entry())
        fake.async_close.assert_awaited_once()
        fake.async_start_polling.assert_not_called()
        assert not hass.data
    asyncio.run(run())


def test_new_discovery_gets_full_snapshot_even_when_cache_unchanged(coordinator, monkeypatch):
    async def run():
        hass = SimpleNamespace(data={}, async_add_executor_job=AsyncMock(return_value=[{"host-name": "sonoff"}]))
        entry = make_entry(esphome_hostname_filter="sonoff")
        instance = coordinator.MikrotikDhcpSyncCoordinator(hass, entry)
        publish = Mock(return_value={})
        monkeypatch.setattr(coordinator, "async_import_leases_to_dhcp_cache", publish)
        instance._esphome_discovery.async_update_leases = Mock()
        assert await instance._async_update_data() == {}
        instance._esphome_discovery.async_update_leases.assert_called_once_with([{"host-name": "sonoff"}])
        assert publish.call_args.kwargs["source_id"] == "router"
    asyncio.run(run())


@pytest.mark.parametrize("option", [None, "192.168.30.0/24", ""])
def test_coordinator_shares_subnet_policy_with_both_discovery_paths(coordinator, monkeypatch, option):
    from ipaddress import ip_network

    async def run():
        leases = [{"host-name": "sonoff", "active-address": "192.168.50.20"}]
        hass = SimpleNamespace(data={}, async_add_executor_job=AsyncMock(return_value=leases))
        entry = make_entry()
        entry.data["discovery_subnets"] = "192.168.10.0/24"
        if option is not None:
            entry.options["discovery_subnets"] = option
        expected = () if option == "" else (ip_network(option or "192.168.10.0/24"),)
        instance = coordinator.MikrotikDhcpSyncCoordinator(hass, entry)
        assert instance._esphome_discovery._discovery_subnets == expected
        publish = Mock(return_value={})
        monkeypatch.setattr(coordinator, "async_import_leases_to_dhcp_cache", publish)
        instance._esphome_discovery.async_update_leases = Mock()
        await instance._async_update_data()
        assert publish.call_args.kwargs["discovery_subnets"] == expected
        assert publish.call_args.args[1] == leases  # Browser still receives every lease.
    asyncio.run(run())
