"""Abbildung von TiXML-Datenpunktnamen auf Home-Assistant-Metadaten.

Die Zuordnung erfolgt über Namensregeln (Reihenfolge wichtig: erste passende
Regel gewinnt), damit sie unabhängig vom konkreten Gerät (EM330/EM340/…) greift.
Siehe docs/DATAMODEL.md für die Herleitung der Einheiten.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class PointMeta:
    component: str = "sensor"          # sensor | binary_sensor
    device_class: str | None = None
    unit: str | None = None
    state_class: str | None = None
    icon: str | None = None
    diagnostic: bool = False


# (Regex auf Datenpunktname, PointMeta). Erste Übereinstimmung zählt.
_RULES: list[tuple[re.Pattern[str], PointMeta]] = [
    # --- Diagnose ---------------------------------------------------------
    (re.compile(r"^DeviceState$|^ConnectionState$"),
     PointMeta(device_class="connectivity", component="binary_sensor", diagnostic=True)),
    (re.compile(r"SerialNo|SerialIP"),
     PointMeta(diagnostic=True, icon="mdi:identifier")),
    (re.compile(r"ChangeToggle|FirstCycle|Mode|Active$|PollActive|PollButton|Mount|TixmlQueue"),
     PointMeta(diagnostic=True)),
    (re.compile(r"CPULoad"),
     PointMeta(unit="%", diagnostic=True, icon="mdi:chip")),
    (re.compile(r"FreeMem"),
     PointMeta(unit="B", diagnostic=True, icon="mdi:memory")),
    (re.compile(r"CycleTime"),
     PointMeta(unit="ms", diagnostic=True, icon="mdi:timer-outline")),

    # --- Digitale I/O (D99 / MRTP) ---------------------------------------
    (re.compile(r"^Input\d+$"),
     PointMeta(component="binary_sensor", icon="mdi:electric-switch")),
    (re.compile(r"^SetRelais\d+$"),
     PointMeta(component="binary_sensor", icon="mdi:electric-switch-closed")),
    (re.compile(r"SignalLED"),
     PointMeta(component="binary_sensor", diagnostic=True, icon="mdi:led-on")),

    # --- Elektrische Messwerte (EM330/EM340) -----------------------------
    #   Energie VOR Leistung prüfen (enthält ebenfalls "Power"-nahes Vokabular).
    # Blindenergie: HA-device_class "energy" erlaubt nur Wh/kWh/… -> hier bewusst
    # ohne device_class, damit kvarh keine HA-Warnung auslöst.
    (re.compile(r"ReactiveEnergy"),
     PointMeta(unit="kvarh", state_class="total_increasing",
               icon="mdi:lightning-bolt-outline")),
    (re.compile(r"RealEnergy|^Energy"),
     PointMeta(device_class="energy", unit="kWh", state_class="total_increasing")),
    (re.compile(r"Voltage"),
     PointMeta(device_class="voltage", unit="V", state_class="measurement")),
    (re.compile(r"Current"),
     PointMeta(device_class="current", unit="A", state_class="measurement")),
    (re.compile(r"ApparentPower|ApparentPwS"),
     PointMeta(device_class="apparent_power", unit="kVA", state_class="measurement")),
    (re.compile(r"ReactivePower|ReactivePwr"),
     PointMeta(device_class="reactive_power", unit="kvar", state_class="measurement")),
    (re.compile(r"RealPower"),
     PointMeta(device_class="power", unit="kW", state_class="measurement")),
    (re.compile(r"CosPhi"),
     PointMeta(device_class="power_factor", state_class="measurement")),
    (re.compile(r"Frequency"),
     PointMeta(device_class="frequency", unit="Hz", state_class="measurement")),
]

_DEFAULT = PointMeta()


def meta_for(point: str) -> PointMeta:
    """Liefert die HA-Metadaten für einen Datenpunktnamen."""
    for pattern, meta in _RULES:
        if pattern.search(point):
            return meta
    return _DEFAULT


def pretty_name(point: str) -> str:
    """Macht aus ``RealPower_L1_N`` ein lesbares ``Real Power L1 N``."""
    text = point.replace("_", " ")
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)   # CamelCase trennen
    text = re.sub(r"\s+", " ", text).strip()
    return text
