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

from .tixi import Device

_LOGGER = logging.getLogger(__name__)

SCHEMA_VERSION = 1


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
                     "display_name": e.display_name, "point_count": len(e.points)}
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
