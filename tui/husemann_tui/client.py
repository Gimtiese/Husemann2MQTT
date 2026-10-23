"""TiXML-HTTP-Client (eigenständige Kopie, unabhängig vom Add-on).

Liest ``/System/Properties/Process/`` (Live-Werte) und
``/System/Config/PROCCFG/External/`` (Gerätetypen). Werte stehen im Attribut ``_``.
Es wird bewusst nicht geloggt, da stderr die TUI zerstören würde; Fehler werden
als Text zurückgegeben.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import requests

_DEVICE_RE = re.compile(r"^D\d+$")
PROCESS_PATH = "System/Properties/Process/"
EXTERNAL_CFG_PATH = "System/Config/PROCCFG/External/"


@dataclass
class Device:
    node: str
    model: str = ""
    values: dict[str, str] = field(default_factory=dict)  # Datenpunkt -> Rohwert


@dataclass
class PollResult:
    gateway: str
    ts: float
    devices: dict[str, Device]
    error: str | None = None
    latency_ms: float = 0.0


@dataclass
class GatewayConfig:
    name: str
    host: str
    port: int = 8080


class TixiClient:
    def __init__(self, cfg: GatewayConfig, timeout: float = 6.0):
        self.cfg = cfg
        self.timeout = timeout
        self._base = f"http://{cfg.host}:{cfg.port}"
        self._models: dict[str, str] = {}
        self._session = requests.Session()

    def _get(self, path: str) -> bytes:
        resp = self._session.get(f"{self._base}/{path}", timeout=self.timeout)
        resp.raise_for_status()
        return resp.content

    def load_models(self) -> None:
        try:
            root = ET.fromstring(self._get(EXTERNAL_CFG_PATH))
        except (requests.RequestException, ET.ParseError):
            return
        models = {f"D{d.get('_')}": d.get("NameUser", "") for d in root.iter("Device") if d.get("_")}
        if models:
            self._models = models

    def poll(self) -> PollResult:
        start = time.time()
        try:
            if not self._models:
                self.load_models()
            root = ET.fromstring(self._get(PROCESS_PATH))
        except requests.RequestException as err:
            return PollResult(self.cfg.name, time.time(), {}, error=str(err).split("\n")[0][:120])
        except ET.ParseError as err:
            return PollResult(self.cfg.name, time.time(), {}, error=f"XML ungültig: {err}")

        devices: dict[str, Device] = {}
        for elem in root.iter():
            if not _DEVICE_RE.match(elem.tag):
                continue
            values = {c.tag: c.get("_") for c in elem if c.get("_") is not None}
            if values:
                devices[elem.tag] = Device(elem.tag, self._models.get(elem.tag, ""), values)
        now = time.time()
        return PollResult(self.cfg.name, now, devices, latency_ms=(now - start) * 1000)
