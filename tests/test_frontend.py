"""Automatic frontend registration without a running Home Assistant server."""

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def frontend(monkeypatch):
    http = ModuleType("homeassistant.components.http")
    http.StaticPathConfig = lambda url, path, cache: (url, path, cache)
    const = ModuleType("homeassistant.components.lovelace.const")
    const.LOVELACE_DATA = "lovelace"
    monkeypatch.setitem(sys.modules, http.__name__, http)
    monkeypatch.setitem(sys.modules, const.__name__, const)
    path = Path(__file__).parents[1] / "custom_components/mikrotik_dhcp_sync/frontend.py"
    spec = importlib.util.spec_from_file_location("frontend_under_test", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


class Resources:
    def __init__(self, items=()):
        self.items = list(items)
        self.writes = 0
        self.loaded = False

    async def async_get_info(self):
        self.loaded = True

    def async_items(self):
        assert self.loaded
        return self.items

    async def async_create_item(self, data):
        self.writes += 1
        self.items.append({"id": "new", "url": data["url"], "type": data["res_type"]})

    async def async_update_item(self, item_id, data):
        self.writes += 1
        item = next(item for item in self.items if item["id"] == item_id)
        item.update(url=data["url"], type=data["res_type"])

    async def async_delete_item(self, item_id):
        self.writes += 1
        self.items = [item for item in self.items if item["id"] != item_id]


def make_hass(resources=None, mode="storage"):
    async def executor(fn):
        return fn()

    return SimpleNamespace(
        data={"lovelace": SimpleNamespace(resource_mode=mode, resources=resources or Resources())},
        http=SimpleNamespace(async_register_static_paths=AsyncMock()),
        async_add_executor_job=executor,
    )


def test_registration_reload_concurrent_entries_and_restart(frontend):
    async def run():
        hass = make_hass()
        await asyncio.gather(*(frontend.async_setup_frontend(hass) for _ in range(4)))
        resources = hass.data["lovelace"].resources
        assert len(resources.items) == resources.writes == 1
        assert resources.items[0]["type"] == "module"
        assert resources.items[0]["url"] == frontend._versioned_url()
        hass.http.async_register_static_paths.assert_awaited_once()
        # Entry unload removes coordinator state, not the process-wide HTTP route.
        hass.data["mikrotik_dhcp_sync"] = {}
        hass.data.pop("mikrotik_dhcp_sync")
        await frontend.async_setup_frontend(hass)
        assert resources.writes == 1
        # A new HA process loads the same persisted resource and registers a route.
        restarted = make_hass(resources)
        await frontend.async_setup_frontend(restarted)
        assert resources.writes == 1
        restarted.http.async_register_static_paths.assert_awaited_once()
    asyncio.run(run())


def test_migrates_old_resource_and_removes_only_matching_duplicates(frontend):
    resources = Resources([
        {"id": "old", "url": "/local/mikrotik-dhcp-browser-card.js?v=1", "type": "js"},
        {"id": "dup", "url": frontend.CARD_URL + "?v=old", "type": "module"},
        {"id": "other", "url": "/local/other.js", "type": "module"},
    ])
    asyncio.run(frontend.async_setup_frontend(make_hass(resources)))
    assert [item["id"] for item in resources.items] == ["old", "other"]
    assert resources.items[0]["url"] == frontend._versioned_url()
    assert resources.items[0]["type"] == "module"


def test_yaml_resources_are_not_modified_but_file_is_served(frontend):
    hass = make_hass(mode="yaml")
    asyncio.run(frontend.async_setup_frontend(hass))
    hass.http.async_register_static_paths.assert_awaited_once()
    assert hass.data["lovelace"].resources.writes == 0


def test_failures_do_not_block_sync_and_retry_without_duplicate_route(frontend):
    async def run():
        hass = make_hass()
        resources = hass.data["lovelace"].resources
        original = resources.async_get_info
        resources.async_get_info = AsyncMock(side_effect=RuntimeError("storage failure"))
        await frontend.async_setup_frontend(hass)
        resources.async_get_info = original
        await frontend.async_setup_frontend(hass)
        assert len(resources.items) == 1
        hass.http.async_register_static_paths.assert_awaited_once()
    asyncio.run(run())


def test_headless_and_failed_static_registration_can_retry(frontend):
    async def run():
        hass = make_hass()
        http = hass.http
        hass.http = None
        await frontend.async_setup_frontend(hass)
        hass.http = http
        http.async_register_static_paths.side_effect = RuntimeError("HTTP failure")
        await frontend.async_setup_frontend(hass)
        assert not hass.data["lovelace"].resources.items
        http.async_register_static_paths.side_effect = None
        await frontend.async_setup_frontend(hass)
        assert len(hass.data["lovelace"].resources.items) == 1
    asyncio.run(run())


def test_content_version_changes_with_asset(frontend, tmp_path, monkeypatch):
    asset = tmp_path / "card.js"
    monkeypatch.setattr(frontend, "CARD_PATH", asset)
    asset.write_text("first", encoding="utf-8")
    old = frontend._versioned_url()
    asset.write_text("second", encoding="utf-8")
    assert frontend._versioned_url() != old
