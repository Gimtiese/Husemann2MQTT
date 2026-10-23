"""Registry: Vorgaben für neue Geräte, Persistenz, Änderungen aus der Oberfläche."""

from __future__ import annotations

import json

from app.registry import Defaults, Registry, device_key


def test_defaults_filter_and_names():
    d = Defaults(auto_enable=True, include=["em340"], exclude=["d99"],
                 names=[{"node": "D11", "name": "Global"}, {"node": "d11", "name": "Nur GW2", "gateway": "Gateway 2"}])
    assert d.allowed("D15", "EM340") and not d.allowed("D11", "EM330") and not d.allowed("D99", "EM340")
    assert d.name_for("Gateway 1", "D11") == "Global"
    assert d.name_for("Gateway 2", "D11") == "Nur GW2"
    assert d.name_for("Gateway 1", "D15") == ""


def test_new_devices_disabled_by_default(tmp_path, d11, d99):
    reg = Registry(str(tmp_path / "devices.json"))
    entries = reg.observe_devices("Gateway 1", {"D11": d11, "D99": d99})
    assert [e.node for e in entries] == ["D11", "D99"]
    assert not any(e.enabled for e in entries)
    e = reg.get("Gateway 1", "D11")
    assert e.model == "EM330" and e.serial == "123456" and e.online
    assert e.default_name == "EM330 D11 (Gateway 1)" and e.display_name == e.default_name
    assert reg.get("Gateway 1", "D99").serial == ""          # "0" = keine Seriennummer


def test_auto_enable_with_defaults(tmp_path, d11, d99):
    reg = Registry(str(tmp_path / "devices.json"),
                   Defaults(auto_enable=True, exclude=["D99"], names=[{"node": "D11", "name": "Haupt"}]))
    reg.observe_devices("Gateway 1", {"D11": d11, "D99": d99})
    assert reg.get("Gateway 1", "D11").enabled and reg.get("Gateway 1", "D11").display_name == "Haupt"
    assert not reg.get("Gateway 1", "D99").enabled


def test_persistence_roundtrip(tmp_path, d11):
    path = tmp_path / "devices.json"
    reg = Registry(str(path))
    reg.observe_devices("Gateway 1", {"D11": d11})
    reg.update([{"key": device_key("Gateway 1", "D11"), "name": "Hauptzähler", "enabled": True}])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["devices"][0]["name"] == "Hauptzähler" and data["devices"][0]["enabled"] is True

    reg2 = Registry(str(path))
    e = reg2.get("Gateway 1", "D11")
    assert e.name == "Hauptzähler" and e.enabled and e.points == ["DeviceState", "RealPower_L1_N", "SerialNo"]
    assert not e.online                     # Online-Status ist nicht persistent


def test_update_produces_pending_changes(tmp_path, d11, d99):
    reg = Registry(str(tmp_path / "devices.json"))
    reg.observe_devices("Gateway 1", {"D11": d11, "D99": d99})
    k11, k99 = device_key("Gateway 1", "D11"), device_key("Gateway 1", "D99")

    assert reg.update([{"key": k11, "enabled": True}, {"key": "unbekannt", "enabled": True}]) == 1
    assert reg.changed.is_set()
    assert [(a, e.node) for a, e in reg.drain_changes()] == [("republish", "D11")]
    assert not reg.changed.is_set()

    assert reg.update([{"key": k11, "name": "Neu"}]) == 1        # Umbenennen -> republish
    assert reg.update([{"key": k11, "name": "Neu"}]) == 0        # unverändert
    assert reg.update([{"key": k11, "enabled": False}, {"key": k99, "name": "x"}]) == 2
    changes = {e.node: a for a, e in reg.drain_changes()}
    assert changes == {"D11": "remove"}      # D99 bleibt deaktiviert -> Name nur gespeichert, nichts an MQTT


def test_forget_removes_entry_and_queues_removal(tmp_path, d11):
    reg = Registry(str(tmp_path / "devices.json"))
    reg.observe_devices("Gateway 1", {"D11": d11})
    key = device_key("Gateway 1", "D11")
    assert reg.forget(key) and not reg.forget(key)
    assert reg.get("Gateway 1", "D11") is None
    (action, entry), = reg.drain_changes()
    assert action == "remove" and entry.points == ["DeviceState", "RealPower_L1_N", "SerialNo"]


def test_snapshot_and_gateway_status(tmp_path, d11):
    reg = Registry(str(tmp_path / "devices.json"))
    reg.observe_gateway("Gateway 1", "192.0.2.10", True, 12.5, 1)
    reg.observe_devices("Gateway 1", {"D11": d11})
    snap = reg.snapshot()
    assert snap["gateways"][0]["online"] and snap["gateways"][0]["last_ok"] > 0
    dev = snap["devices"][0]
    assert dev["key"] == "gateway_1/d11" and dev["point_count"] == 3 and dev["display_name"].startswith("EM330")
    reg.observe_devices("Gateway 1", {})                     # Gerät verschwunden
    assert reg.snapshot()["devices"][0]["online"] is False


def test_corrupt_file_starts_empty(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text("{kaputt", encoding="utf-8")
    assert Registry(str(path)).snapshot()["devices"] == []
