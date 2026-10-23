"""MQTT-Bridge: Discovery-Payloads, Gateway-Gerät, Entfernen und Neu-Senden."""

from __future__ import annotations

from app.mqtt_bridge import manufacturer_for
from app.registry import Entry


def test_manufacturer_from_model():
    assert manufacturer_for("EM340") == "Carlo Gavazzi"
    assert manufacturer_for("MRTP_XX") == "Tixi"
    assert manufacturer_for("") == "Husemann"


def test_discovery_payload(bridge, mqtt, d11):
    assert bridge.publish_device("Gateway 1", d11, "Hauptzähler") == 3
    c = mqtt.configs()["homeassistant/sensor/husemann_gateway_1_d11_realpower_l1_n/config"]
    assert c["device"]["name"] == "Hauptzähler"
    assert c["device"]["manufacturer"] == "Carlo Gavazzi"
    assert c["device"]["serial_number"] == "123456"
    assert c["device"]["via_device"] == "husemann_gw_gateway_1"
    assert c["has_entity_name"] is True and c["object_id"] == c["unique_id"]
    assert c["device_class"] == "power" and c["unit_of_measurement"] == "kW"
    assert c["availability_topic"] == "husemann/bridge/status"
    state = mqtt.configs()["homeassistant/binary_sensor/husemann_gateway_1_d11_devicestate/config"]
    assert state["entity_category"] == "diagnostic" and state["payload_on"] == "1"
    assert ("husemann/gateway_1/d11/RealPower_L1_N", "-35.0") in mqtt.states()


def test_discovery_only_once_until_forgotten(bridge, mqtt, d11):
    bridge.publish_device("Gateway 1", d11, "A")
    mqtt.clear()
    bridge.publish_device("Gateway 1", d11, "A")
    assert not mqtt.configs() and len(mqtt.states()) == 3

    bridge.forget_device(Entry("Gateway 1", "D11"))
    bridge.publish_device("Gateway 1", d11, "B")
    assert all(c["device"]["name"] == "B" for c in mqtt.configs().values())


def test_remove_device_publishes_empty_retained(bridge, mqtt, d11):
    bridge.publish_device("Gateway 1", d11, "A")
    mqtt.clear()
    entry = Entry("Gateway 1", "D11", points=["RealPower_L1_N", "DeviceState", "SerialNo"])
    assert bridge.remove_device(entry) == 3
    assert sorted(mqtt.removals()) == [
        "homeassistant/binary_sensor/husemann_gateway_1_d11_devicestate/config",
        "homeassistant/sensor/husemann_gateway_1_d11_realpower_l1_n/config",
        "homeassistant/sensor/husemann_gateway_1_d11_serialno/config",
    ]
    mqtt.clear()
    bridge.publish_device("Gateway 1", d11, "A")      # danach wird Discovery wieder gesendet
    assert len(mqtt.configs()) == 3


def test_no_serial_when_zero(bridge, mqtt, d99):
    bridge.publish_device("Gateway 1", d99, "IO")
    c = mqtt.configs()["homeassistant/binary_sensor/husemann_gateway_1_d99_input01/config"]
    assert "serial_number" not in c["device"] and c["device"]["manufacturer"] == "Tixi"


def test_gateway_device(bridge, mqtt):
    bridge.publish_gateway("Gateway 1", "192.0.2.10", True, 42.4, 3)
    g = mqtt.configs()["homeassistant/binary_sensor/husemann_gw_gateway_1_online/config"]
    assert g["device"]["identifiers"] == ["husemann_gw_gateway_1"]
    assert g["device_class"] == "connectivity" and g["device"]["configuration_url"] == "http://192.0.2.10/"
    assert ("husemann/gateway_1/gateway/online", "1") in mqtt.states()
    assert ("husemann/gateway_1/gateway/latency", "42") in mqtt.states()
    mqtt.clear()
    bridge.publish_gateway("Gateway 1", "192.0.2.10", False, None, 0)
    assert not mqtt.configs()                                # Discovery nur einmal
    assert ("husemann/gateway_1/gateway/online", "0") in mqtt.states()
    assert not [t for t, _ in mqtt.states() if t.endswith("/latency")]
