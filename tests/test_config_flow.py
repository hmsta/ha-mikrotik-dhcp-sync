"""Configuration defaults and clearing the optional discovery filter."""

import asyncio
import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest

from test_dhcp_bridge import dhcp_bridge  # noqa: F401


@pytest.fixture
def config_flow(dhcp_bridge, monkeypatch):
    pytest.importorskip("voluptuous")
    entries = ModuleType("homeassistant.config_entries")

    class Flow:
        def __init_subclass__(cls, **kwargs):
            pass

        def async_show_form(self, **kwargs):
            return kwargs

        def async_create_entry(self, **kwargs):
            return kwargs

    entries.ConfigFlow = entries.OptionsFlow = Flow
    monkeypatch.setitem(sys.modules, entries.__name__, entries)
    name = "custom_components.mikrotik_dhcp_sync.config_flow"
    monkeypatch.delitem(sys.modules, name, raising=False)
    return importlib.import_module(name)


def test_new_and_existing_installations_default_to_disabled(config_flow):
    field = config_flow.CONF_ESPHOME_HOSTNAME_FILTER
    setup = config_flow._setup_schema()({"host": "router", "username": "user", "password": "secret"})
    assert setup[field] == ""
    assert config_flow._options_schema()({})[field] == ""


def test_option_round_trip_and_clear_overrides_setup_value(config_flow):
    async def run():
        field = config_flow.CONF_ESPHOME_HOSTNAME_FILTER
        entry = SimpleNamespace(data={field: "esp32"}, options={field: "sonoff"})
        flow = config_flow.OptionsFlowHandler(entry)
        form = await flow.async_step_init()
        marker = next(key for key in form["data_schema"].schema if key == field)
        assert marker.description["suggested_value"] == "sonoff"
        saved = await flow.async_step_init(form["data_schema"]({field: ""}))
        assert saved["data"][field] == ""
        entry.options = saved["data"]
        reopened = await flow.async_step_init()
        assert reopened["data_schema"]({})[field] == ""
    asyncio.run(run())


def test_cleared_optional_text_omitted_by_ha_frontend_stays_empty(config_flow):
    fields = (config_flow.CONF_ESPHOME_HOSTNAME_FILTER, config_flow.CONF_HOSTNAME_FALLBACK_RULES)
    schema = config_flow._options_schema({field: "previous value" for field in fields})
    # ha-form-string sends undefined when an optional text field is cleared;
    # JSON drops it, so the backend must not restore the old saved value.
    result = schema({})
    assert all(result[field] == "" for field in fields)
