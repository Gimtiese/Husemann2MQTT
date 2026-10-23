"""Einstiegspunkt: Optionen laden, Gateways pollen, nach MQTT publizieren, Weboberfläche anbieten."""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time

from .mqtt_bridge import MqttBridge
from .registry import Defaults, Registry
from .tixi import TixiGateway
from .web import WebServer

OPTIONS_PATH = os.environ.get("H2M_OPTIONS", "/data/options.json")
REGISTRY_PATH = os.environ.get("H2M_REGISTRY", "/data/devices.json")
WEB_PORT = int(os.environ.get("H2M_WEB_PORT", "8099"))

_LOG_LEVELS = {
    "trace": logging.DEBUG, "debug": logging.DEBUG, "info": logging.INFO,
    "notice": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR,
    "fatal": logging.ERROR,
}

_running = True


def _handle_signal(signum, frame):  # noqa: ARG001
    global _running
    _running = False


def load_options(path: str = OPTIONS_PATH) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as err:
        logging.getLogger(__name__).error("Optionen nicht lesbar (%s) – Standardwerte.", err)
        return {}


def resolve_mqtt(options: dict, env: dict | None = None) -> dict | None:
    """Externe `mqtt`-Optionen haben Vorrang; sonst HA-Dienst (ENV aus run.sh)."""
    env = os.environ if env is None else env
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
    if env.get("HA_MQTT_HOST"):
        return {
            **base,
            "host": env["HA_MQTT_HOST"],
            "port": int(env.get("HA_MQTT_PORT", "1883") or 1883),
            "username": env.get("HA_MQTT_USER", ""),
            "password": env.get("HA_MQTT_PASSWORD", ""),
            "tls": str(env.get("HA_MQTT_SSL", "")).lower() in ("true", "1"),
        }
    return None


def defaults_from_options(options: dict) -> Defaults:
    return Defaults(
        auto_enable=bool(options.get("auto_enable_new_devices", False)),
        include=list(options.get("include_devices") or []),
        exclude=list(options.get("exclude_devices") or []),
        names=list(options.get("device_names") or []),
    )


def apply_changes(registry: Registry, bridge: MqttBridge) -> None:
    """Änderungen aus der Weboberfläche auf MQTT anwenden (vor dem nächsten Publizieren)."""
    for action, entry in registry.drain_changes():
        if action == "remove":
            bridge.remove_device(entry)
        else:
            bridge.forget_device(entry)      # Discovery wird im Zyklus mit neuem Namen neu gesendet


def poll_cycle(gateways: list[TixiGateway], registry: Registry, bridge: MqttBridge,
               log: logging.Logger, announced: set[str]) -> None:
    for gw in gateways:
        devices = gw.read_process()
        registry.observe_gateway(gw.name, gw.host, bool(devices), gw.last_latency_ms, len(devices),
                                 "" if devices else "keine Antwort")
        published = points = 0
        for entry in registry.observe_devices(gw.name, devices):
            if not entry.enabled:
                continue
            points += bridge.publish_device(gw.name, devices[entry.node], entry.display_name)
            published += 1
        bridge.publish_gateway(gw.name, gw.host, bool(devices), gw.last_latency_ms, published)
        if devices:
            log.log(logging.DEBUG if gw.name in announced else logging.INFO,
                    "Gateway %s: %d Geräte gefunden, %d publiziert (%d Datenpunkte)",
                    gw.name, len(devices), published, points)
            announced.add(gw.name)


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

    gateways = [
        TixiGateway(gw.get("name", gw["host"]), gw["host"], gw.get("port", 8080), timeout)
        for gw in options.get("gateways", [])
    ]
    if not gateways:
        log.error("Keine Gateways konfiguriert (Option 'gateways').")
        return 1

    for gw in gateways:
        gw.load_models()

    registry = Registry(REGISTRY_PATH, defaults_from_options(options))

    bridge = MqttBridge(mqtt_cfg)
    try:
        bridge.connect()
    except Exception as err:  # noqa: BLE001
        log.error("MQTT-Verbindung fehlgeschlagen: %s", err)
        return 1

    web = WebServer(registry, WEB_PORT, allow_any_client=os.environ.get("H2M_ALLOW_ANY_CLIENT") == "1")
    try:
        web.start()
    except OSError as err:
        log.error("Weboberfläche konnte nicht starten (Port %d): %s", WEB_PORT, err)
        bridge.disconnect()
        return 1

    log.info("Starte Polling: %d Gateway(s), Intervall %ds", len(gateways), poll_interval)
    if not registry.defaults.auto_enable:
        log.info("Neue Geräte werden erst nach Auswahl in der Weboberfläche publiziert "
                 "(Option auto_enable_new_devices).")
    announced: set[str] = set()

    while _running:
        cycle_start = time.monotonic()
        apply_changes(registry, bridge)
        poll_cycle(gateways, registry, bridge, log, announced)

        # Bis zum nächsten Zyklus warten – Änderungen aus der Oberfläche wecken sofort.
        remaining = poll_interval - (time.monotonic() - cycle_start)
        while _running and remaining > 0:
            if registry.changed.wait(min(0.5, remaining)):
                break
            remaining = poll_interval - (time.monotonic() - cycle_start)

    log.info("Beende …")
    web.stop()
    bridge.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
