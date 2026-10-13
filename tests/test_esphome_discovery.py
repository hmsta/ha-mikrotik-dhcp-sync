"""Optional discovery filtering, protocol validation, and task lifecycle."""

import asyncio
import importlib
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_dhcp_bridge import dhcp_bridge  # noqa: F401


@pytest.fixture
def discovery(dhcp_bridge, monkeypatch):
    name = "custom_components.mikrotik_dhcp_sync.esphome_discovery"
    monkeypatch.delitem(sys.modules, name, raising=False)
    config_entries = ModuleType("homeassistant.config_entries")
    config_entries.SOURCE_ZEROCONF = "zeroconf"
    zeroconf = ModuleType("homeassistant.helpers.service_info.zeroconf")
    zeroconf.ZeroconfServiceInfo = lambda **kwargs: SimpleNamespace(**kwargs)
    monkeypatch.setitem(sys.modules, config_entries.__name__, config_entries)
    monkeypatch.setitem(sys.modules, zeroconf.__name__, zeroconf)
    return importlib.import_module(name)


def lease(mac="AA:BB:CC:DD:EE:FF", host="192.168.10.20", hostname="bedroom-Sonoff"):
    return {"mac-address": mac, "active-address": host, "host-name": hostname}


def make_hass():
    entries, progress, tasks = [], [], []

    async def start(domain, *, context, data):
        progress.append({"context": {"unique_id": data.properties["mac"]}})
        return {"type": "form", "step_id": "discovery_confirm"}

    def create_task(coro, name):
        task = asyncio.create_task(coro, name=name)
        tasks.append(task)
        return task

    return SimpleNamespace(
        data={}, entries=entries, progress=progress, tasks=tasks,
        async_create_background_task=create_task,
        config_entries=SimpleNamespace(
            async_entries=lambda domain: entries,
            flow=SimpleNamespace(
                async_progress_by_handler=lambda domain: progress,
                async_init=AsyncMock(side_effect=start),
            ),
        ),
    )


def manager(discovery, monkeypatch, words="esp32 sonoff"):
    hass = make_hass()
    instance = discovery.ESPHomeDiscovery(hass, words)
    instance._async_load_api = AsyncMock(return_value=object())
    probe = AsyncMock(return_value=discovery.DeviceInfo("real-node", "Bedroom", False))
    monkeypatch.setattr(discovery, "_async_probe", probe)
    return hass, instance, probe


async def drain(instance):
    if instance._task is not None:
        await instance._task


@pytest.mark.parametrize("words", ["", "   \n, ; "])
def test_empty_option_does_not_load_or_probe(discovery, monkeypatch, words):
    async def run():
        hass, instance, probe = manager(discovery, monkeypatch, words)
        instance.async_update_leases([lease()])
        assert not hass.tasks and not hass.data
        instance._async_load_api.assert_not_awaited()
        probe.assert_not_awaited()
    asyncio.run(run())


def test_filter_initial_snapshot_and_handoff(discovery, monkeypatch):
    async def run():
        hass, instance, probe = manager(discovery, monkeypatch, "ESP32, sonoff\nfoo")
        instance.async_update_leases([
            lease(), lease("00:11:22:33:44:55", hostname="printer"),
            lease("00:11:22:33:44:66", hostname=""),
            lease("00:11:22:33:44:77", host="", hostname="esp32"),
            lease("00:11:22:33:44:88", host="224.0.0.251", hostname="esp32"),
        ])
        await drain(instance)
        assert probe.await_count == 1
        call = hass.config_entries.flow.async_init.call_args
        assert call.args == ("esphome",)
        assert call.kwargs["context"] == {"source": "zeroconf"}
        info = call.kwargs["data"]
        assert str(info.ip_address) == "192.168.10.20"
        assert info.hostname == "real-node.local."
        assert info.type == "_esphomelib._tcp.local."
        assert info.port == 6053
        assert info.properties == {"mac": "aabbccddeeff", "friendly_name": "Bedroom"}
        # Successful or dismissed suggestions aren't reprobed on every poll.
        hass.progress.clear()
        instance.async_update_leases([lease()])
        await drain(instance)
        assert probe.await_count == 1
    asyncio.run(run())


@pytest.mark.parametrize("known", ["configured", "ignored", "disabled", "pending"])
def test_known_devices_skip_probes(discovery, monkeypatch, known):
    async def run():
        hass, instance, probe = manager(discovery, monkeypatch)
        if known == "pending":
            hass.progress.append({"context": {"unique_id": "aa:bb:cc:dd:ee:ff"}})
        else:
            hass.entries.append(SimpleNamespace(unique_id="aa:bb:cc:dd:ee:ff", data={}, source=known))
        instance.async_update_leases([lease()])
        await drain(instance)
        probe.assert_not_awaited()
        instance._async_load_api.assert_not_awaited()
    asyncio.run(run())


def test_active_mac_is_used_for_known_device_check(discovery, monkeypatch):
    async def run():
        hass, instance, probe = manager(discovery, monkeypatch)
        hass.entries.append(SimpleNamespace(unique_id="aa:bb:cc:dd:ee:ff", data={}))
        record = lease(mac="00:00:00:00:00:00")
        record["active-mac-address"] = "AA:BB:CC:DD:EE:FF"
        instance.async_update_leases([record])
        await drain(instance)
        probe.assert_not_awaited()
        assert set(instance._states) == {"aabbccddeeff"}
    asyncio.run(run())


def test_failures_back_off_and_changes_reset_candidate(discovery, monkeypatch):
    async def run():
        _, instance, probe = manager(discovery, monkeypatch)
        now = [100.0]
        monkeypatch.setattr(discovery, "monotonic", lambda: now[0])
        probe.return_value = None
        instance.async_update_leases([lease()])
        await drain(instance)
        instance.async_update_leases([lease()])
        await drain(instance)
        assert probe.await_count == 1
        now[0] = 160
        instance.async_update_leases([lease()])
        await drain(instance)
        assert probe.await_count == 2
        assert instance._states["aabbccddeeff"].next_attempt == 460
        instance.async_update_leases([lease(host="192.168.10.21")])
        await drain(instance)
        assert probe.await_count == 3
        instance.async_update_leases([lease(host="192.168.10.21", hostname="ESP32-new")])
        await drain(instance)
        assert probe.await_count == 4
        instance.async_update_leases([])
        assert not instance._states
    asyncio.run(run())


def test_dependency_failure_is_retried_without_losing_candidates(discovery, monkeypatch):
    async def run():
        hass, instance, probe = manager(discovery, monkeypatch)
        instance._async_load_api.side_effect = RuntimeError("requirements unavailable")
        instance.async_update_leases([lease()])
        await drain(instance)
        probe.assert_not_awaited()
        assert instance._states["aabbccddeeff"].attempts == 1
        assert not hass.data[discovery._RUNTIME_KEY].in_flight
    asyncio.run(run())


@pytest.mark.parametrize("action", ["remove", "change", "configure", "close"])
def test_inflight_changes_and_unload(discovery, monkeypatch, action):
    async def run():
        hass, instance, probe = manager(discovery, monkeypatch)
        entered, release = asyncio.Event(), asyncio.Event()

        async def wait_probe(*args):
            entered.set()
            await release.wait()
            return discovery.DeviceInfo("node", "Node", False)

        probe.side_effect = wait_probe
        instance.async_update_leases([lease()])
        await entered.wait()
        if action == "remove":
            instance.async_update_leases([])
        elif action == "change":
            instance.async_update_leases([lease(host="192.168.10.21")])
        elif action == "configure":
            hass.entries.append(SimpleNamespace(unique_id="aabbccddeeff", data={}))
        else:
            await instance.async_close()
            assert instance._task.cancelled()
        release.set()
        if action != "close":
            await drain(instance)
        hass.config_entries.flow.async_init.assert_not_awaited()
        assert not hass.data[discovery._RUNTIME_KEY].in_flight
    asyncio.run(run())


def test_shared_router_entries_deduplicate_inflight_mac(discovery, monkeypatch):
    async def run():
        hass, first, probe = manager(discovery, monkeypatch)
        second = discovery.ESPHomeDiscovery(hass, "sonoff")
        second._async_load_api = AsyncMock()
        entered, release = asyncio.Event(), asyncio.Event()

        async def wait_probe(*args):
            entered.set()
            await release.wait()
            return discovery.DeviceInfo("node", "Node", False)

        probe.side_effect = wait_probe
        first.async_update_leases([lease()])
        await entered.wait()
        second.async_update_leases([lease()])
        await drain(second)
        release.set()
        await drain(first)
        second.async_update_leases([lease()])
        await drain(second)
        assert probe.await_count == 1
        assert hass.config_entries.flow.async_init.await_count == 1
    asyncio.run(run())


def fake_api(outcomes):
    class APIConnectionError(Exception):
        pass

    class RequiresEncryptionAPIError(APIConnectionError):
        pass

    class InvalidEncryptionKeyAPIError(APIConnectionError):
        def __init__(self, name=None, mac=None):
            self.received_name, self.received_mac = name, mac

    clients = []

    def create(*args, **kwargs):
        outcome = outcomes[len(clients)]
        client = SimpleNamespace(
            kwargs=kwargs, connect=AsyncMock(), disconnect=AsyncMock(),
            device_info=AsyncMock(return_value=outcome),
        )
        if isinstance(outcome, Exception):
            client.connect.side_effect = outcome
        clients.append(client)
        return client

    return SimpleNamespace(
        APIClient=create, clients=clients, APIConnectionError=APIConnectionError,
        RequiresEncryptionAPIError=RequiresEncryptionAPIError,
        InvalidEncryptionKeyAPIError=InvalidEncryptionKeyAPIError,
    )


def test_plaintext_probe_requires_matching_device_identity(discovery):
    async def run():
        candidate = discovery.Candidate("aabbccddeeff", "192.168.10.20", "sonoff")
        for mac, expected in [("AA:BB:CC:DD:EE:FF", True), ("00:11:22:33:44:55", False)]:
            api = fake_api([SimpleNamespace(name="real-node", friendly_name="Room", mac_address=mac)])
            result = await discovery._async_probe(api, candidate)
            assert bool(result) == expected
            api.clients[0].connect.assert_awaited_once_with(login=False)
            api.clients[0].disconnect.assert_awaited_once_with(force=True)
    asyncio.run(run())


@pytest.mark.parametrize("name,mac,expected", [
    ("node", "aa:bb:cc:dd:ee:ff", True), ("node", None, True),
    (None, None, False), ("node", "00:11:22:33:44:55", False),
])
def test_encrypted_probe_requires_identifiable_hello(discovery, name, mac, expected):
    async def run():
        outcomes = []
        api = fake_api(outcomes)
        outcomes.extend([api.RequiresEncryptionAPIError(), api.InvalidEncryptionKeyAPIError(name, mac)])
        result = await discovery._async_probe(api, discovery.Candidate("aabbccddeeff", "192.168.10.20", "sonoff"))
        assert bool(result) == expected
        if result:
            assert result.encrypted
        assert api.clients[1].kwargs["noise_psk"] == discovery._PROBE_KEY
        for client in api.clients:
            client.disconnect.assert_awaited_once_with(force=True)
    asyncio.run(run())


def test_open_port_or_protocol_failure_is_not_discovery(discovery):
    async def run():
        outcomes = []
        api = fake_api(outcomes)
        outcomes.append(api.APIConnectionError("not ESPHome"))
        result = await discovery._async_probe(api, discovery.Candidate("aabbccddeeff", "192.168.10.20", "sonoff"))
        assert result is None
        api.clients[0].disconnect.assert_awaited_once_with(force=True)
    asyncio.run(run())


def test_probe_timeout_disconnects_and_backs_off(discovery, monkeypatch):
    async def run():
        hass = make_hass()
        instance = discovery.ESPHomeDiscovery(hass, "sonoff")
        api = fake_api([None])
        original_create = api.APIClient

        def create(*args, **kwargs):
            client = original_create(*args, **kwargs)
            async def hang(**kwargs):
                await asyncio.Event().wait()

            client.connect.side_effect = hang
            return client

        api.APIClient = create
        instance._async_load_api = AsyncMock(return_value=api)
        monkeypatch.setattr(discovery, "_PROBE_TIMEOUT", 0.01)
        instance.async_update_leases([lease()])
        await drain(instance)
        api.clients[0].disconnect.assert_awaited_once_with(force=True)
        hass.config_entries.flow.async_init.assert_not_awaited()
        assert instance._states["aabbccddeeff"].attempts == 1
    asyncio.run(run())


def test_encrypted_handoff_and_failed_handoff_retry(discovery, monkeypatch):
    async def run():
        hass, instance, probe = manager(discovery, monkeypatch)
        probe.return_value = discovery.DeviceInfo("node", "Node", True)
        hass.config_entries.flow.async_init.side_effect = RuntimeError("flow unavailable")
        instance.async_update_leases([lease()])
        await drain(instance)
        state = instance._states["aabbccddeeff"]
        assert state.attempts == 1 and state.next_attempt != float("inf")
        data = hass.config_entries.flow.async_init.call_args.kwargs["data"]
        assert data.properties["api_encryption"].startswith("Noise_")
        assert "noise_psk" not in data.properties
    asyncio.run(run())


def test_concurrency_is_bounded_across_routers(discovery, monkeypatch):
    async def run():
        hass = make_hass()
        release = asyncio.Event()
        two_started = asyncio.Event()
        active = maximum = 0

        async def probe(*args):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            if active == 2:
                two_started.set()
            await release.wait()
            active -= 1
            return None

        monkeypatch.setattr(discovery, "_async_probe", probe)
        instances = []
        for n in range(4):
            instance = discovery.ESPHomeDiscovery(hass, "sonoff")
            instance._async_load_api = AsyncMock()
            instances.append(instance)
            instance.async_update_leases([lease(mac=f"AA:BB:CC:DD:EE:{n:02X}")])
        await two_started.wait()
        assert active == 2
        release.set()
        await asyncio.gather(*(drain(instance) for instance in instances))
        assert maximum == 2 and active == 0
    asyncio.run(run())


@pytest.mark.parametrize("encrypted", [False, True])
def test_real_client_against_local_protocol_server(discovery, monkeypatch, encrypted):
    """Exercise real wire parsing, including the encrypted hello error fields.

    Optional dependency: install HA's aioesphomeapi version to run this check.
    The only network connection is to a temporary localhost server.
    """
    api = pytest.importorskip("aioesphomeapi")
    from aioesphomeapi.api_pb2 import DeviceInfoResponse, HelloResponse

    def varint(value):
        result = bytearray()
        while value > 127:
            result.append((value & 127) | 128)
            value >>= 7
        return bytes(result) + bytes([value])

    async def read_varint(reader):
        result = shift = 0
        while True:
            value = (await reader.readexactly(1))[0]
            result |= (value & 127) << shift
            if value < 128:
                return result
            shift += 7

    async def server(reader, writer):
        try:
            if encrypted:
                preamble = await reader.readexactly(1)
                if preamble == b"\x00":
                    writer.write(b"\x01\x00\x00")
                else:
                    hello = b"\x01wire-node\x00aabbccddeeff\x00"
                    error = b"\x01Handshake MAC failure"
                    for frame in (hello, error):
                        writer.write(b"\x01" + len(frame).to_bytes(2, "big") + frame)
                await writer.drain()
                await reader.read()
            else:
                while True:
                    assert await reader.readexactly(1) == b"\x00"
                    length = await read_varint(reader)
                    kind = await read_varint(reader)
                    await reader.readexactly(length)
                    if kind == 1:
                        response = HelloResponse(api_version_major=1, api_version_minor=10, name="wire-node")
                        response_kind = 2
                    elif kind == 9:
                        response = DeviceInfoResponse(name="wire-node", friendly_name="Wire Node", mac_address="AA:BB:CC:DD:EE:FF")
                        response_kind = 10
                    else:
                        continue
                    payload = response.SerializeToString()
                    writer.write(b"\x00" + varint(len(payload)) + varint(response_kind) + payload)
                    await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    async def run():
        async with await asyncio.start_server(server, "127.0.0.1", 0) as listener:
            monkeypatch.setattr(discovery, "_PORT", listener.sockets[0].getsockname()[1])
            async with asyncio.timeout(5):
                result = await discovery._async_probe(
                    api, discovery.Candidate("aabbccddeeff", "127.0.0.1", "sonoff")
                )
            assert result is not None
            assert result.name == "wire-node" and result.encrypted == encrypted
            await asyncio.sleep(0)
    asyncio.run(run())
