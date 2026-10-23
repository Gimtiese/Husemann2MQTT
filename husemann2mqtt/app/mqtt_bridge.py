"""MQTT-Anbindung inkl. Home-Assistant-Discovery."""

from __future__ import annotations

import json
import logging
import re

import paho.mqtt.client as mqtt

from . import __version__
from .mapping import PointMeta, meta_for, pretty_name
from .registry import Entry, slug
from .tixi import Device

_LOGGER = logging.getLogger(__name__)

# Hersteller anhand des Gerätetyps (NameUser der Tixi-Konfig); Fallback = Panel-Hersteller.
_MANUFACTURERS = (
    (re.compile(r"^(EM|ET|WM)\d", re.I), "Carlo Gavazzi"),
    (re.compile(r"MRTP|^MR\d", re.I), "Tixi"),
)


def manufacturer_for(model: str) -> str:
    for pattern, name in _MANUFACTURERS:
        if pattern.search(model or ""):
            return name
    return "Husemann"


class MqttBridge:
    def __init__(self, cfg: dict):
        self.host = cfg["host"]
        self.port = int(cfg["port"])
        self.base_topic = cfg["base_topic"].strip("/")
        self.discovery_prefix = cfg["discovery_prefix"].strip("/")
        self.status_topic = f"{self.base_topic}/bridge/status"
        self._published: set[str] = set()     # unique_ids mit bereits gesendeter Discovery

        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="husemann2mqtt",
        )
        if cfg.get("username"):
            self._client.username_pw_set(cfg["username"], cfg.get("password") or None)
        if cfg.get("tls"):
            self._client.tls_set()
        self._client.will_set(self.status_topic, "offline", qos=1, retain=True)
        self._client.on_connect = self._on_connect

    # ------------------------------------------------------------------ #
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            _LOGGER.info("Mit MQTT-Broker %s:%s verbunden", self.host, self.port)
            client.publish(self.status_topic, "online", qos=1, retain=True)
        else:
            _LOGGER.error("MQTT-Verbindung abgelehnt: %s", reason_code)

    def connect(self) -> None:
        self._client.connect(self.host, self.port, keepalive=60)
        self._client.loop_start()

    def disconnect(self) -> None:
        try:
            self._client.publish(self.status_topic, "offline", qos=1, retain=True)
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:  # noqa: BLE001 - best effort beim Shutdown
            pass

    # ------------------------------------------------------------------ IDs / Topics
    @staticmethod
    def _gateway_id(gw_name: str) -> str:
        return f"husemann_gw_{slug(gw_name)}"

    @staticmethod
    def _device_id(gw_name: str, node: str) -> str:
        return f"husemann_{slug(gw_name)}_{slug(node)}"

    def _unique_id(self, gw_name: str, node: str, point: str) -> str:
        return f"{self._device_id(gw_name, node)}_{slug(point)}"

    def _discovery_topic(self, component: str, unique_id: str) -> str:
        return f"{self.discovery_prefix}/{component}/{unique_id}/config"

    def _state_topic(self, gw_name: str, node: str, point: str) -> str:
        return f"{self.base_topic}/{slug(gw_name)}/{slug(node)}/{point}"

    # ------------------------------------------------------------------ Geräteblöcke
    def _gateway_block(self, gw_name: str, host: str) -> dict:
        return {
            "identifiers": [self._gateway_id(gw_name)],
            "name": gw_name,
            "model": "Tixi.Gate",
            "manufacturer": "Husemann",
            "configuration_url": f"http://{host}/",
            "sw_version": __version__,
        }

    def _device_block(self, gw_name: str, dev: Device, name: str) -> dict:
        block = {
            "identifiers": [self._device_id(gw_name, dev.node)],
            "name": name,
            "model": dev.model or "Tixi device",
            "manufacturer": manufacturer_for(dev.model),
            "via_device": self._gateway_id(gw_name),
            "sw_version": __version__,
        }
        serial = dev.values.get("SerialNo")
        if serial and serial != "0":
            block["serial_number"] = serial
        return block

    # ------------------------------------------------------------------ Discovery
    def _publish_discovery(self, gw_name: str, dev: Device, name: str, point: str, meta: PointMeta) -> None:
        unique_id = self._unique_id(gw_name, dev.node, point)
        if unique_id in self._published:
            return
        config: dict = {
            "name": pretty_name(point),
            "has_entity_name": True,          # Anzeigename = "<Gerätename> <Punkt>"
            "unique_id": unique_id,
            "object_id": unique_id,           # entity_id bleibt stabil, auch wenn umbenannt wird
            "state_topic": self._state_topic(gw_name, dev.node, point),
            "availability_topic": self.status_topic,
            "device": self._device_block(gw_name, dev, name),
        }
        if meta.device_class:
            config["device_class"] = meta.device_class
        if meta.unit:
            config["unit_of_measurement"] = meta.unit
        if meta.state_class:
            config["state_class"] = meta.state_class
        if meta.icon:
            config["icon"] = meta.icon
        if meta.diagnostic:
            config["entity_category"] = "diagnostic"
        if meta.component == "binary_sensor":
            config["payload_on"] = "1"
            config["payload_off"] = "0"

        topic = self._discovery_topic(meta.component, unique_id)
        self._client.publish(topic, json.dumps(config), qos=1, retain=True)
        self._published.add(unique_id)
        _LOGGER.debug("Discovery: %s", topic)

    def publish_device(self, gw_name: str, dev: Device, name: str) -> int:
        """Discovery (einmalig) + aktuelle Werte für ein Gerät. Gibt Anzahl Punkte zurück."""
        count = 0
        for point, value in dev.values.items():
            self._publish_discovery(gw_name, dev, name, point, meta_for(point))
            self._client.publish(self._state_topic(gw_name, dev.node, point), value, qos=0, retain=False)
            count += 1
        return count

    def forget_device(self, entry: Entry) -> None:
        """Vergisst die gesendete Discovery, sodass sie (z. B. mit neuem Namen) erneut gesendet wird."""
        prefix = self._device_id(entry.gateway, entry.node) + "_"
        self._published = {u for u in self._published if not u.startswith(prefix)}

    def remove_device(self, entry: Entry) -> int:
        """Entfernt alle Entitäten eines Geräts aus HA (leere, retained Discovery-Payloads)."""
        count = 0
        for point in entry.points:
            unique_id = self._unique_id(entry.gateway, entry.node, point)
            topic = self._discovery_topic(meta_for(point).component, unique_id)
            self._client.publish(topic, "", qos=1, retain=True)
            self._published.discard(unique_id)
            count += 1
        _LOGGER.info("Gerät %s %s aus Home Assistant entfernt (%d Entitäten)",
                     entry.gateway, entry.node, count)
        return count

    # ------------------------------------------------------------------ Gateway
    def publish_gateway(self, gw_name: str, host: str, online: bool, latency_ms: float | None,
                        device_count: int) -> None:
        """Gateway als eigenes HA-Gerät (Eltern-Gerät der Zähler) mit Diagnose-Entitäten."""
        base = f"{self.base_topic}/{slug(gw_name)}/gateway"
        entities = [
            ("binary_sensor", "online", "Online",
             {"device_class": "connectivity", "payload_on": "1", "payload_off": "0"}),
            ("sensor", "latency", "Antwortzeit",
             {"unit_of_measurement": "ms", "state_class": "measurement", "icon": "mdi:timer-outline"}),
            ("sensor", "devices", "Geräte", {"icon": "mdi:counter", "state_class": "measurement"}),
        ]
        for component, key, name, extra in entities:
            unique_id = f"{self._gateway_id(gw_name)}_{key}"
            if unique_id not in self._published:
                config = {
                    "name": name, "has_entity_name": True, "unique_id": unique_id,
                    "object_id": unique_id, "state_topic": f"{base}/{key}",
                    "availability_topic": self.status_topic, "entity_category": "diagnostic",
                    "device": self._gateway_block(gw_name, host), **extra,
                }
                self._client.publish(self._discovery_topic(component, unique_id),
                                     json.dumps(config), qos=1, retain=True)
                self._published.add(unique_id)
        self._client.publish(f"{base}/online", "1" if online else "0", qos=0, retain=False)
        if latency_ms is not None:
            self._client.publish(f"{base}/latency", f"{latency_ms:.0f}", qos=0, retain=False)
        self._client.publish(f"{base}/devices", str(device_count), qos=0, retain=False)
