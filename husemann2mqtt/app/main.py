"""Einstiegspunkt: Optionen laden, Gateways pollen, nach MQTT publizieren."""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time

from .mqtt_bridge import MqttBridge
from .tixi import TixiGateway

OPTIONS_PATH = "/data/options.json"

_LOG_LEVELS = {
    "trace": logging.DEBUG, "debug": logging.DEBUG, "info": logging.INFO,
    "notice": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR,
    "fatal": logging.ERROR,
}

_running = True


def _handle_signal(signum, frame):  # noqa: ARG001
    global _running
    _running = False


def load_options() -> dict:
    try:
        with open(OPTIONS_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as err:
        logging.getLogger(__name__).error("Optionen nicht lesbar (%s) – Standardwerte.", err)
        return {}


def resolve_mqtt(options: dict) -> dict | None:
    """Externe `mqtt`-Optionen haben Vorrang; sonst HA-Dienst (ENV aus run.sh)."""
    ext = options.get("mqtt") or {}
    base = {
        "base_topic": options.get("base_topic", "husemann"),
        "discovery_prefix": options.get("discovery_prefix", "homeassistant"),
    }
    if ext.get("host"):
        return {
            **base,
            "host": ext["host"], "port": ext.get("port", 1883),
            "username": ext.get("username", ""), "password": ext.get("password", ""),
            "tls": ext.get("tls", False),
        }
    if os.environ.get("HA_MQTT_HOST"):
        return {
            **base,
            "host": os.environ["HA_MQTT_HOST"],
            "port": int(os.environ.get("HA_MQTT_PORT", "1883") or 1883),
            "username": os.environ.get("HA_MQTT_USER", ""),
            "password": os.environ.get("HA_MQTT_PASSWORD", ""),
            "tls": str(os.environ.get("HA_MQTT_SSL", "")).lower() in ("true", "1"),
        }
    return None


def device_allowed(node: str, model: str, include: list[str], exclude: list[str]) -> bool:
    """include (falls gesetzt) = Whitelist; exclude = Blacklist. Matcht Node ODER Modell."""
    tags = {node.lower(), model.lower()}
    if exclude and tags & {e.lower() for e in exclude}:
        return False
    if include:
        return bool(tags & {i.lower() for i in include})
    return True


def main() -> int:
    options = load_options()
    level = _LOG_LEVELS.get(str(options.get("log_level", "info")).lower(), logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("husemann2mqtt")

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    mqtt_cfg = resolve_mqtt(options)
    if not mqtt_cfg:
        log.error("Kein MQTT-Broker verfügbar: weder HA-MQTT-Dienst noch Option 'mqtt.host' gesetzt.")
        return 1

    poll_interval = int(options.get("poll_interval", 10))
    timeout = int(options.get("request_timeout", 8))
    include = options.get("include_devices") or []
    exclude = options.get("exclude_devices") or []

    gateways = [
        TixiGateway(gw.get("name", gw["host"]), gw["host"], gw.get("port", 8080), timeout)
        for gw in options.get("gateways", [])
    ]
    if not gateways:
        log.error("Keine Gateways konfiguriert (Option 'gateways').")
        return 1

    for gw in gateways:
        gw.load_models()

    bridge = MqttBridge(mqtt_cfg, options.get("device_names") or [])
    try:
        bridge.connect()
    except Exception as err:  # noqa: BLE001
        log.error("MQTT-Verbindung fehlgeschlagen: %s", err)
        return 1

    log.info("Starte Polling: %d Gateway(s), Intervall %ds", len(gateways), poll_interval)
    announced: set[str] = set()      # Gateways, deren erste erfolgreiche Abfrage geloggt wurde

    while _running:
        cycle_start = time.monotonic()
        for gw in gateways:
            devices = gw.read_process()
            published = points = 0
            for node, dev in sorted(devices.items()):
                if not device_allowed(node, dev.model, include, exclude):
                    continue
                points += bridge.publish_device(gw.name, dev)
                published += 1
            bridge.publish_gateway(gw.name, gw.host, bool(devices), gw.last_latency_ms, published)
            if devices:
                log.log(logging.DEBUG if gw.name in announced else logging.INFO,
                        "Gateway %s: %d Geräte, %d Datenpunkte publiziert", gw.name, published, points)
                announced.add(gw.name)

        elapsed = time.monotonic() - cycle_start
        sleep_for = max(0.0, poll_interval - elapsed)
        waited = 0.0
        while _running and waited < sleep_for:
            step = min(0.5, sleep_for - waited)
            time.sleep(step)
            waited += step

    log.info("Beende …")
    bridge.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
