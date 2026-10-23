"""Hauptschleife: Poll-Zyklus mit Registry und Anwenden von Oberflächen-Änderungen."""

from __future__ import annotations

import logging

from app.main import apply_changes, defaults_from_options, poll_cycle, resolve_mqtt
from app.registry import Defaults, Registry, device_key


def test_resolve_mqtt_prefers_external_then_env():
    assert resolve_mqtt({"mqtt": {"host": ""}}, env={}) is None
    ext = resolve_mqtt({"mqtt": {"host": "broker", "username": "u"}}, env={"HA_MQTT_HOST": "ha"})
    assert ext["host"] == "broker" and ext["port"] == 1883 and ext["username"] == "u"
    ha = resolve_mqtt({}, env={"HA_MQTT_HOST": "core-mosquitto", "HA_MQTT_PORT": "1883", "HA_MQTT_SSL": "true"})
    assert ha["host"] == "core-mosquitto" and ha["tls"] is True
    assert ha["base_topic"] == "husemann" and ha["discovery_prefix"] == "homeassistant"


def test_defaults_from_options():
    d = defaults_from_options({"auto_enable_new_devices": True, "include_devices": ["EM340"],
                               "device_names": [{"node": "D11", "name": "X"}]})
    assert d.auto_enable and d.include == ["EM340"] and d.name_for("gw", "d11") == "X"
    assert defaults_from_options({}) == Defaults()


def test_poll_cycle_publishes_only_enabled(tmp_path, gateway, bridge, mqtt):
    reg = Registry(str(tmp_path / "devices.json"))
    log = logging.getLogger("test")
    poll_cycle([gateway], reg, bridge, log, set())
    # Nichts angehakt -> nur das Gateway-Gerät, keine Zähler
    assert all("husemann_gw_" in t for t in mqtt.configs())
    assert ("husemann/gateway_1/gateway/devices", "0") in mqtt.states()
    assert reg.snapshot()["gateways"][0]["device_count"] == 2
    assert gateway.last_latency_ms is not None

    # In der Oberfläche D11 anhaken und benennen -> nächster Zyklus publiziert mit Namen
    reg.update([{"key": device_key("Gateway 1", "D11"), "name": "Hauptzähler", "enabled": True}])
    mqtt.clear()
    apply_changes(reg, bridge)
    poll_cycle([gateway], reg, bridge, log, set())
    cfgs = mqtt.configs()
    assert cfgs["homeassistant/sensor/husemann_gateway_1_d11_frequency/config"]["device"]["name"] == "Hauptzähler"
    assert not [t for t in cfgs if "_d99_" in t]
    assert ("husemann/gateway_1/gateway/devices", "1") in mqtt.states()

    # Umbenennen -> Discovery wird mit neuem Namen erneut gesendet, ohne Neustart
    reg.update([{"key": device_key("Gateway 1", "D11"), "name": "Zähler Nord"}])
    mqtt.clear()
    apply_changes(reg, bridge)
    poll_cycle([gateway], reg, bridge, log, set())
    cfg = mqtt.configs()["homeassistant/sensor/husemann_gateway_1_d11_frequency/config"]
    assert cfg["device"]["name"] == "Zähler Nord"

    # Abwählen -> Entitäten werden entfernt, keine States mehr
    reg.update([{"key": device_key("Gateway 1", "D11"), "enabled": False}])
    mqtt.clear()
    apply_changes(reg, bridge)
    poll_cycle([gateway], reg, bridge, log, set())
    assert len(mqtt.removals()) == 4
    assert not [t for t, _ in mqtt.states() if "/d11/" in t]


def test_poll_cycle_gateway_offline(tmp_path, gateway, bridge, mqtt):
    def fail(url, timeout):
        raise __import__("requests").RequestException("timeout")

    gateway._session.get = fail
    reg = Registry(str(tmp_path / "devices.json"))
    poll_cycle([gateway], reg, bridge, logging.getLogger("test"), set())
    gw = reg.snapshot()["gateways"][0]
    assert gw["online"] is False and gw["error"] and gw["last_ok"] == 0
    assert ("husemann/gateway_1/gateway/online", "0") in mqtt.states()
