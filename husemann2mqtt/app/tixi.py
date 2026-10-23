"""Client für die TiXML-HTTP-API der Tixi.Gate (HE653).

Die Tixi stellt ihren Objektbaum als XML per HTTP-GET bereit, z. B.:

    GET http://<host>:8080/System/Properties/Process/        -> Live-Werte
    GET http://<host>:8080/System/Config/PROCCFG/External/    -> Gerätekonfig

Werte stehen im Attribut ``_``:  ``<Frequency _="49.9"/>`` -> ``49.9``.
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import requests

_LOGGER = logging.getLogger(__name__)

# Knoten der Form D11, D12 … D99 = ein Gerät
_DEVICE_RE = re.compile(r"^D\d+$")

PROCESS_PATH = "System/Properties/Process/"
EXTERNAL_CFG_PATH = "System/Config/PROCCFG/External/"


@dataclass
class Device:
    """Ein am Bus gefundenes Gerät (ein ``Dxx``-Knoten)."""

    node: str                       # z. B. "D11"
    model: str = ""                 # NameUser aus der Konfig, z. B. "EM330"
    values: dict[str, str] = field(default_factory=dict)  # Datenpunkt -> Rohwert


class TixiGateway:
    """Liest Prozess- und Konfigurationsdaten einer Tixi.Gate aus."""

    def __init__(self, name: str, host: str, port: int = 8080, timeout: int = 8):
        self.name = name
        self.host = host
        self.port = int(port)
        self.timeout = timeout
        self._base = f"http://{host}:{self.port}"
        self._models: dict[str, str] = {}      # node -> model (gecached)
        self._session = requests.Session()
        self.last_latency_ms: float | None = None   # Dauer des letzten erfolgreichen Prozess-Reads

    # ------------------------------------------------------------------ #
    def _get(self, path: str) -> bytes | None:
        url = f"{self._base}/{path}"
        try:
            resp = self._session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            return resp.content
        except requests.RequestException as err:
            _LOGGER.warning("Gateway %s: GET %s fehlgeschlagen: %s", self.name, path, err)
            return None

    # ------------------------------------------------------------------ #
    def load_models(self) -> dict[str, str]:
        """Liest die Gerätetypen (NameUser) aus der External-Konfig.

        Wird einmalig beim Start versucht; schlägt es fehl, bleibt ``model`` leer.
        """
        raw = self._get(EXTERNAL_CFG_PATH)
        if not raw:
            return self._models
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as err:
            _LOGGER.warning("Gateway %s: External-Konfig nicht parsebar: %s", self.name, err)
            return self._models

        models: dict[str, str] = {}
        # <Device _="11" NameUser="EM330" …>
        for dev in root.iter("Device"):
            num = dev.get("_")
            model = dev.get("NameUser", "")
            if num:
                models[f"D{num}"] = model
        if models:
            self._models = models
            _LOGGER.info("Gateway %s: %d Gerätetypen aus Konfig geladen", self.name, len(models))
        return self._models

    # ------------------------------------------------------------------ #
    def read_process(self) -> dict[str, Device]:
        """Liest den Live-Prozessbaum und liefert ``{node: Device}``."""
        start = time.monotonic()
        raw = self._get(PROCESS_PATH)
        if not raw:
            self.last_latency_ms = None
            return {}
        self.last_latency_ms = (time.monotonic() - start) * 1000
        if not self._models:                       # Gerätetypen nachladen, falls Start fehlschlug
            self.load_models()
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as err:
            _LOGGER.warning("Gateway %s: Prozessbaum nicht parsebar: %s", self.name, err)
            return {}

        devices: dict[str, Device] = {}
        # Jeder Dxx-Knoten irgendwo im Baum ist ein Gerät.
        for elem in root.iter():
            if not _DEVICE_RE.match(elem.tag):
                continue
            values: dict[str, str] = {}
            for child in elem:
                val = child.get("_")
                if val is not None:
                    values[child.tag] = val
            if not values:
                continue
            devices[elem.tag] = Device(
                node=elem.tag,
                model=self._models.get(elem.tag, ""),
                values=values,
            )
        return devices
