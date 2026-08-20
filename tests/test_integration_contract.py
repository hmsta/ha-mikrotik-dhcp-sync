"""Contract tests for MikroTik DHCP Sync's intentionally tiny surface."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[1]
INTEGRATION = ROOT / "custom_components" / "mikrotik_dhcp_sync"


def test_manifest_has_required_routeros_and_dhcp_contract():
    manifest = (INTEGRATION / "manifest.json").read_text(encoding="utf-8")

    assert '"domain": "mikrotik_dhcp_sync"' in manifest
    assert '"dependencies": ["dhcp"]' in manifest
    assert '"librouteros==4.1.1"' in manifest


def test_no_platform_modules_are_present():
    platform_names = {
        "device_tracker.py",
        "sensor.py",
        "binary_sensor.py",
        "button.py",
        "switch.py",
    }

    assert not platform_names & {path.name for path in INTEGRATION.iterdir()}


def test_no_persistent_dhcp_storage_or_recorder_usage():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in INTEGRATION.glob("*.py")
        if path.name != "strings.json"
    )

    assert "Store(" not in source
    assert "recorder" not in source.lower()
    assert "async_get_or_create" not in source
    assert "device_tracker" not in source

