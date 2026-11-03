"""MQTT-Bridge: Discovery-Payloads, Gateway-Gerät, Entfernen und Neu-Senden."""

from __future__ import annotations

from app.mqtt_bridge import MqttBridge, manufacturer_for
from app.registry import Entry
from app.tixi import Device


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


# ------------------------------------------------------------- Topic-Naming (0.4.0)
def _discovery(mqtt, key):
    return mqtt.configs()[key]


def test_state_topic_uses_gui_name(bridge, mqtt, d11):
    """Topic-Segment = GUI-Name (slug), Fallback Node-ID; unique_id bleibt an der Node-ID."""
    bridge.publish_device("Gateway 1", d11, "Hauptzähler Garage", topic_name="Hauptzähler Garage")
    c = _discovery(mqtt, "homeassistant/sensor/husemann_gateway_1_d11_realpower_l1_n/config")
    assert c["state_topic"] == "husemann/gateway_1/hauptzähler_garage/RealPower_L1_N"
    assert ("husemann/gateway_1/hauptzähler_garage/RealPower_L1_N", "-35.0") in mqtt.states()
    # ohne Namen: weiterhin Node-ID im Topic
    mqtt.clear()
    bridge.publish_device("Gateway 1", d11, "Hauptzähler Garage")
    assert ("husemann/gateway_1/d11/RealPower_L1_N", "-35.0") in mqtt.states()


def test_unique_ids_stable_across_rename(bridge, mqtt, d11):
    """Umbenennen ändert weder unique_id, object_id noch device.identifiers."""
    bridge.publish_device("Gateway 1", d11, "Alter Name", topic_name="Alter Name")
    before = _discovery(mqtt, "homeassistant/sensor/husemann_gateway_1_d11_realpower_l1_n/config")
    bridge.forget_device(Entry("Gateway 1", "D11"))
    mqtt.clear()
    bridge.publish_device("Gateway 1", d11, "Neuer Name", topic_name="Neuer Name")
    after = _discovery(mqtt, "homeassistant/sensor/husemann_gateway_1_d11_realpower_l1_n/config")
    for key in ("unique_id", "object_id"):
        assert before[key] == after[key] == "husemann_gateway_1_d11_realpower_l1_n"
    assert before["device"]["identifiers"] == after["device"]["identifiers"] == ["husemann_gateway_1_d11"]
    # Discovery wurde mit neuem state_topic erneut gesendet, nichts entfernt
    assert after["state_topic"] == "husemann/gateway_1/neuer_name/RealPower_L1_N"
    assert after["device"]["name"] == "Neuer Name"
    assert not mqtt.removals()


def test_name_collision_gets_distinct_topics(bridge, mqtt, d11, d99):
    """Zwei Geräte mit gleichem Namen auf einem Gateway teilen kein Topic."""
    bridge.publish_device("Gateway 1", d11, "Hausanschluss", topic_name="Hausanschluss")
    bridge.publish_device("Gateway 1", d99, "Hausanschluss", topic_name="Hausanschluss")
    # d11 belegt den Namen, d99 weicht auf die Node-ID aus -> keine gemeinsamen Topics
    segments = sorted({c["state_topic"].split("/")[2] for c in mqtt.configs().values()})
    assert segments == ["d99", "hausanschluss"]
    assert ("husemann/gateway_1/d99/Input01", "1") in mqtt.states()


def test_topic_naming_node_keeps_node_topics(mqtt):
    bridge = MqttBridge({"host": "broker", "port": 1883, "base_topic": "husemann",
                         "discovery_prefix": "homeassistant", "topic_naming": "node"})
    dev = Device("D11", "EM330", {"RealPower_L1_N": "-35.0"})
    bridge.publish_device("Gateway 1", dev, "Hauptzähler", topic_name="Hauptzähler")
    c = _discovery(mqtt, "homeassistant/sensor/husemann_gateway_1_d11_realpower_l1_n/config")
    assert c["state_topic"] == "husemann/gateway_1/d11/RealPower_L1_N"
    assert c["device"]["name"] == "Hauptzähler"
