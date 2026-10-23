"""MQTT-Anbindung inkl. Home-Assistant-Discovery."""

from __future__ import annotations

import json
import logging
import re

import paho.mqtt.client as mqtt

from . import __version__
from .mapping import PointMeta, meta_for, pretty_name
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


def slug(text: str) -> str:
    out = "".join(c if c.isalnum() else "_" for c in text.lower())
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_")


class MqttBridge:
    def __init__(self, cfg: dict, device_names: list[dict] | None = None):
        self.host = cfg["host"]
        self.port = int(cfg["port"])
        self.base_topic = cfg["base_topic"].strip("/")
        self.discovery_prefix = cfg["discovery_prefix"].strip("/")
        self.status_topic = f"{self.base_topic}/bridge/status"
        self._published: set[str] = set()     # unique_ids mit bereits gesendeter Discovery
        # Eigene Gerätenamen aus der Option `device_names`: (gateway|None, node) -> name
        self._names: dict[tuple[str | None, str], str] = {}
        for entry in device_names or []:
            node, name = str(entry.get("node", "")).strip(), str(entry.get("name", "")).strip()
            gw = str(entry.get("gateway") or "").strip() or None
            if node and name:
                self._names[(gw.lower() if gw else None, node.lower())] = name

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

    # ------------------------------------------------------------------ #
    def device_name(self, gw_name: str, dev: Device) -> str:
        """Name aus `device_names` (gateway-spezifisch vor global), sonst ``EM340 D15 (Gateway)``."""
        custom = (self._names.get((gw_name.lower(), dev.node.lower()))
                  or self._names.get((None, dev.node.lower())))
        if custom:
            return custom
        return f"{dev.model + ' ' if dev.model else ''}{dev.node} ({gw_name})".strip()

    def _gateway_id(self, gw_name: str) -> str:
        return f"husemann_gw_{slug(gw_name)}"

    def _gateway_block(self, gw_name: str, host: str) -> dict:
        return {
            "identifiers": [self._gateway_id(gw_name)],
            "name": gw_name,
            "model": "Tixi.Gate",
            "manufacturer": "Husemann",
            "configuration_url": f"http://{host}/",
            "sw_version": __version__,
        }

    def _device_block(self, gw_name: str, dev: Device) -> dict:
        dev_id = f"husemann_{slug(gw_name)}_{slug(dev.node)}"
        block = {
            "identifiers": [dev_id],
            "name": self.device_name(gw_name, dev),
            "model": dev.model or "Tixi device",
            "manufacturer": manufacturer_for(dev.model),
            "via_device": self._gateway_id(gw_name),
            "sw_version": __version__,
        }
        serial = dev.values.get("SerialNo")
        if serial and serial not in ("0", ""):
            block["serial_number"] = serial
        return block

    def _state_topic(self, gw_name: str, dev: Device, point: str) -> str:
        return f"{self.base_topic}/{slug(gw_name)}/{slug(dev.node)}/{point}"

    # ------------------------------------------------------------------ #
    def publish_discovery(self, gw_name: str, dev: Device, point: str, meta: PointMeta) -> None:
        unique_id = f"husemann_{slug(gw_name)}_{slug(dev.node)}_{slug(point)}"
        if unique_id in self._published:
            return

        config: dict = {
            "name": pretty_name(point),
            "has_entity_name": True,          # Anzeigename = "<Gerätename> <Punkt>"
            "unique_id": unique_id,
            "object_id": unique_id,           # entity_id bleibt stabil, auch wenn umbenannt wird
            "state_topic": self._state_topic(gw_name, dev, point),
            "availability_topic": self.status_topic,
            "device": self._device_block(gw_name, dev),
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

        topic = f"{self.discovery_prefix}/{meta.component}/{unique_id}/config"
        self._client.publish(topic, json.dumps(config), qos=1, retain=True)
        self._published.add(unique_id)
        _LOGGER.debug("Discovery: %s", topic)

    def publish_state(self, gw_name: str, dev: Device, point: str, value: str) -> None:
        topic = self._state_topic(gw_name, dev, point)
        self._client.publish(topic, value, qos=0, retain=False)

    # ------------------------------------------------------------------ #
    def publish_gateway(self, gw_name: str, host: str, online: bool, latency_ms: float | None,
                        device_count: int) -> None:
        """Gateway als eigenes HA-Gerät (Eltern-Gerät der Zähler) mit Diagnose-Entitäten."""
        gw_slug = slug(gw_name)
        base = f"{self.base_topic}/{gw_slug}/gateway"
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
                self._client.publish(f"{self.discovery_prefix}/{component}/{unique_id}/config",
                                     json.dumps(config), qos=1, retain=True)
                self._published.add(unique_id)
        self._client.publish(f"{base}/online", "1" if online else "0", qos=0, retain=False)
        if latency_ms is not None:
            self._client.publish(f"{base}/latency", f"{latency_ms:.0f}", qos=0, retain=False)
        self._client.publish(f"{base}/devices", str(device_count), qos=0, retain=False)

    def publish_device(self, gw_name: str, dev: Device) -> int:
        """Discovery (einmalig) + aktuelle Werte für ein Gerät. Gibt Anzahl Punkte zurück."""
        count = 0
        for point, value in dev.values.items():
            meta = meta_for(point)
            self.publish_discovery(gw_name, dev, point, meta)
            self.publish_state(gw_name, dev, point, value)
            count += 1
        return count
