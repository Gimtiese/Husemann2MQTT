"""Geräte-Registry: welche gefundenen Geräte nach MQTT gehen und wie sie heißen.

Die Registry ist die gemeinsame Wahrheit von Poll-Schleife (schreibt, was gefunden
wurde) und Weboberfläche (schreibt Name/Auswahl). Sie liegt persistent in
``/data/devices.json`` und überlebt damit Neustarts und Add-on-Updates.

Änderungen aus der Oberfläche werden als "pending changes" gesammelt und von der
Poll-Schleife im nächsten Zyklus auf MQTT angewendet (Discovery neu/entfernen).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field

from .mapping import meta_for, pretty_name
from .tixi import Device

_LOGGER = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Reihenfolge der Messwerte in der Detailansicht: erst das, was beim Zuordnen hilft.
_POINT_ORDER = ("RealPower", "ApparentPower", "ReactivePower", "Voltage", "Current", "CosPhi",
                "Frequency", "Energy", "Input", "SetRelais")


def _point_rank(point: str) -> tuple[int, str]:
    for i, prefix in enumerate(_POINT_ORDER):
        if prefix.lower() in point.lower():
            return (i, point)
    return (len(_POINT_ORDER) + (1 if meta_for(point).diagnostic else 0), point)


def format_value(point: str, raw: str) -> str:
    """Rohwert lesbar machen: Einheit anhängen, Schaltzustände als an/aus."""
    meta = meta_for(point)
    if meta.component == "binary_sensor":
        return "aus" if raw in ("0", "0.0", "") else "an"
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return raw
    text = f"{val:.2f}".rstrip("0").rstrip(".") if abs(val) < 1000 else f"{val:.0f}"
    return f"{text} {meta.unit}" if meta.unit else text


def describe_values(values: dict[str, str]) -> list[dict]:
    """Alle Datenpunkte eines Geräts für die Oberfläche, sinnvoll sortiert."""
    return [
        {"point": p, "name": pretty_name(p), "value": format_value(p, values[p]),
         "diagnostic": meta_for(p).diagnostic}
        for p in sorted(values, key=_point_rank)
    ]


def summarize(values: dict[str, str]) -> str:
    """Kurzfassung für die Geräteliste, z. B. ``P 35.2 kW · U 232 V · E 12345 kWh``."""
    parts = []
    power = next((p for p in ("RealPowerPSum_P1P2P3", "RealPower_L1_N") if p in values), None)
    if power:
        parts.append("P " + format_value(power, values[power]))
    if "Voltage_L1_N" in values:
        parts.append("U " + format_value("Voltage_L1_N", values["Voltage_L1_N"]))
    if "RealEnergy_L1L2L3Con" in values:
        parts.append("E " + format_value("RealEnergy_L1L2L3Con", values["RealEnergy_L1L2L3Con"]))
    inputs = sorted(p for p in values if p.startswith("Input"))
    if inputs:
        on = [p.removeprefix("Input").lstrip("0") for p in inputs if values[p] not in ("0", "0.0")]
        parts.append(f"Eingänge an: {', '.join(on) if on else 'keine'}")
    relays = sorted(p for p in values if p.startswith("SetRelais"))
    if relays:
        on = [p.removeprefix("SetRelais").lstrip("0") for p in relays if values[p] not in ("0", "0.0")]
        parts.append(f"Relais an: {', '.join(on) if on else 'keine'}")
    return " · ".join(parts)


def slug(text: str) -> str:
    out = "".join(c if c.isalnum() else "_" for c in text.lower())
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_")


def device_key(gateway: str, node: str) -> str:
    return f"{slug(gateway)}/{node.lower()}"


@dataclass
class Entry:
    """Ein bekanntes Gerät (persistent)."""

    gateway: str
    node: str
    name: str = ""                  # vom Nutzer vergebener Name ("" = Standardname)
    enabled: bool = False           # nach MQTT publizieren?
    model: str = ""
    serial: str = ""
    points: list[str] = field(default_factory=list)   # zuletzt gesehene Datenpunkte
    first_seen: float = 0.0
    last_seen: float = 0.0
    online: bool = False            # im aktuellen Scan vorhanden und DeviceState != 0

    @property
    def key(self) -> str:
        return device_key(self.gateway, self.node)

    @property
    def default_name(self) -> str:
        return f"{self.model + ' ' if self.model else ''}{self.node} ({self.gateway})".strip()

    @property
    def display_name(self) -> str:
        return self.name or self.default_name


@dataclass
class GatewayStatus:
    name: str
    host: str
    online: bool = False
    latency_ms: float | None = None
    last_ok: float = 0.0
    device_count: int = 0           # im letzten Scan gefunden
    error: str = ""


@dataclass
class Defaults:
    """Vorgaben aus den Add-on-Optionen für neu gefundene Geräte."""

    auto_enable: bool = False
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    names: list[dict] = field(default_factory=list)     # Option `device_names`

    def allowed(self, node: str, model: str) -> bool:
        tags = {node.lower(), model.lower()}
        if self.exclude and tags & {e.lower() for e in self.exclude}:
            return False
        if self.include:
            return bool(tags & {i.lower() for i in self.include})
        return True

    def name_for(self, gateway: str, node: str) -> str:
        best = ""
        for entry in self.names:
            if str(entry.get("node", "")).strip().lower() != node.lower():
                continue
            gw = str(entry.get("gateway") or "").strip()
            if gw and gw.lower() != gateway.lower():
                continue
            name = str(entry.get("name", "")).strip()
            if gw:                      # gateway-spezifisch schlägt global
                return name
            best = best or name
        return best


class Registry:
    def __init__(self, path: str | None, defaults: Defaults | None = None):
        self.path = path
        self.defaults = defaults or Defaults()
        self._lock = threading.RLock()
        self._entries: dict[str, Entry] = {}
        self._values: dict[str, dict[str, str]] = {}   # key -> letzte Rohwerte (nur im Speicher)
        self._gateways: dict[str, GatewayStatus] = {}
        self._pending: dict[str, str] = {}          # key -> "republish" | "remove"
        self._removed: dict[str, Entry] = {}        # für "remove" die letzte bekannte Entry
        self.changed = threading.Event()            # weckt die Poll-Schleife
        self._load()

    # ------------------------------------------------------------- persistenz
    def _load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            for raw in data.get("devices", []):
                entry = Entry(**{k: v for k, v in raw.items() if k in Entry.__dataclass_fields__})
                entry.online = False
                self._entries[entry.key] = entry
            _LOGGER.info("Registry geladen: %d Geräte aus %s", len(self._entries), self.path)
        except (OSError, ValueError, TypeError) as err:
            _LOGGER.warning("Registry %s nicht lesbar (%s) – starte leer.", self.path, err)

    def save(self) -> None:
        if not self.path:
            return
        with self._lock:
            data = {"version": SCHEMA_VERSION,
                    "devices": [asdict(e) for e in sorted(self._entries.values(), key=lambda e: e.key)]}
        tmp = f"{self.path}.tmp"
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError as err:
            _LOGGER.error("Registry konnte nicht gespeichert werden: %s", err)

    # ------------------------------------------------------------- poll-seite
    def observe_gateway(self, name: str, host: str, online: bool, latency_ms: float | None,
                        device_count: int, error: str = "") -> None:
        with self._lock:
            st = self._gateways.setdefault(name, GatewayStatus(name, host))
            st.host, st.online, st.latency_ms, st.error = host, online, latency_ms, error
            st.device_count = device_count
            if online:
                st.last_ok = time.time()

    def observe_devices(self, gateway: str, devices: dict[str, Device]) -> list[Entry]:
        """Trägt gefundene Geräte ein (neue mit Vorgaben) und liefert die Einträge."""
        now = time.time()
        new: list[Entry] = []
        result: list[Entry] = []
        with self._lock:
            seen = set()
            for node, dev in devices.items():
                key = device_key(gateway, node)
                seen.add(key)
                entry = self._entries.get(key)
                if entry is None:
                    entry = Entry(
                        gateway=gateway, node=node,
                        name=self.defaults.name_for(gateway, node),
                        enabled=self.defaults.auto_enable and self.defaults.allowed(node, dev.model),
                        first_seen=now,
                    )
                    self._entries[key] = entry
                    new.append(entry)
                entry.model = dev.model or entry.model
                serial = dev.values.get("SerialNo", "")
                if serial and serial != "0":
                    entry.serial = serial
                entry.points = sorted(dev.values)
                self._values[key] = dict(dev.values)
                entry.last_seen = now
                entry.online = dev.values.get("DeviceState", "1") not in ("0", "0.0")
                result.append(entry)
            for entry in self._entries.values():
                if entry.gateway == gateway and entry.key not in seen:
                    entry.online = False
        if new:
            _LOGGER.info("Gateway %s: %d neue Geräte gefunden: %s", gateway, len(new),
                         ", ".join(f"{e.node} {e.model}".strip() for e in new))
            self.save()
        return result

    def drain_changes(self) -> list[tuple[str, Entry]]:
        """Liefert und leert die ausstehenden Änderungen: (Aktion, Entry)."""
        with self._lock:
            out = []
            for key, action in self._pending.items():
                entry = self._entries.get(key) or self._removed.pop(key, None)
                if entry:
                    out.append((action, entry))
            self._pending.clear()
            self.changed.clear()
            return out

    # ------------------------------------------------------------- web-seite
    def snapshot(self) -> dict:
        with self._lock:
            return {
                "gateways": [asdict(g) for g in self._gateways.values()],
                "devices": [
                    {**asdict(e), "key": e.key, "default_name": e.default_name,
                     "display_name": e.display_name, "point_count": len(e.points),
                     "summary": summarize(self._values.get(e.key, {})),
                     "values": describe_values(self._values.get(e.key, {}))}
                    for e in sorted(self._entries.values(), key=lambda e: (e.gateway, _node_order(e.node)))
                ],
                "pending": bool(self._pending),
            }

    def update(self, changes: list[dict]) -> int:
        """Übernimmt Name/Auswahl aus der Oberfläche. Liefert Anzahl geänderter Geräte."""
        count = 0
        with self._lock:
            for ch in changes:
                entry = self._entries.get(str(ch.get("key", "")))
                if entry is None:
                    continue
                name = str(ch.get("name", entry.name)).strip()
                enabled = bool(ch.get("enabled", entry.enabled))
                if name == entry.name and enabled == entry.enabled:
                    continue
                if entry.enabled and not enabled:
                    self._pending[entry.key] = "remove"
                elif enabled:
                    self._pending[entry.key] = "republish"
                entry.name, entry.enabled = name, enabled
                count += 1
        if count:
            self.save()
            self.changed.set()
        return count

    def forget(self, key: str) -> bool:
        """Entfernt ein Gerät samt MQTT-Discovery (z. B. nach Ausbau aus dem Panel)."""
        with self._lock:
            entry = self._entries.pop(key, None)
            self._values.pop(key, None)
            if entry is None:
                return False
            self._removed[key] = entry
            self._pending[key] = "remove"
        self.save()
        self.changed.set()
        return True

    def get(self, gateway: str, node: str) -> Entry | None:
        with self._lock:
            return self._entries.get(device_key(gateway, node))


def _node_order(node: str) -> tuple[int, str]:
    digits = "".join(c for c in node if c.isdigit())
    return (int(digits) if digits else 10**6, node)
