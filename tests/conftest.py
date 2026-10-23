"""Gemeinsame Test-Helfer: gefakter MQTT-Client und gefakte Tixi-Antworten."""

from __future__ import annotations

import json

import pytest

from app.mqtt_bridge import MqttBridge
from app.tixi import Device, TixiGateway

XML_CFG = b'<External><Device _="11" NameUser="EM330"/><Device _="99" NameUser="MRTP_XX"/></External>'
XML_PROC = (
    b"<Process><Modbus>"
    b'<D11><Frequency _="49.9"/><RealPower_L1_N _="-35.0"/><SerialNo _="123456"/><DeviceState _="1"/></D11>'
    b'<D99><Input01 _="0"/><SerialNo _="0"/><DeviceState _="1"/></D99>'
    b"</Modbus><IO/></Process>"
)


class FakeMqttClient:
    """Zeichnet Publishes auf; Discovery-Configs werden als JSON zugänglich gemacht."""

    def __init__(self, *args, **kwargs):
        self.published: list[tuple[str, str, bool]] = []

    def username_pw_set(self, *a): ...
    def tls_set(self): ...
    def will_set(self, *a, **k): ...
    def connect(self, *a, **k): ...
    def loop_start(self): ...
    def loop_stop(self): ...
    def disconnect(self): ...

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, retain))

    # --- Auswertung -----------------------------------------------------
    def configs(self) -> dict[str, dict]:
        return {t: json.loads(p) for t, p, r in self.published if t.endswith("/config") and p}

    def removals(self) -> list[str]:
        return [t for t, p, r in self.published if t.endswith("/config") and p == "" and r]

    def states(self) -> list[tuple[str, str]]:
        return [(t, p) for t, p, r in self.published if not t.endswith("/config")]

    def clear(self) -> None:
        self.published.clear()


@pytest.fixture
def mqtt(monkeypatch) -> FakeMqttClient:
    fake = FakeMqttClient()
    monkeypatch.setattr("app.mqtt_bridge.mqtt.Client", lambda *a, **k: fake)
    return fake


@pytest.fixture
def bridge(mqtt) -> MqttBridge:
    return MqttBridge({"host": "broker", "port": 1883, "base_topic": "husemann",
                       "discovery_prefix": "homeassistant"})


@pytest.fixture
def gateway() -> TixiGateway:
    """TixiGateway mit gefaktem HTTP (liefert XML_CFG / XML_PROC)."""

    class Resp:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            pass

    gw = TixiGateway("Gateway 1", "192.0.2.10")
    gw._session.get = lambda url, timeout: Resp(XML_CFG if "PROCCFG" in url else XML_PROC)
    return gw


@pytest.fixture
def d11() -> Device:
    return Device("D11", "EM330", {"RealPower_L1_N": "-35.0", "SerialNo": "123456", "DeviceState": "1"})


@pytest.fixture
def d99() -> Device:
    return Device("D99", "MRTP_XX", {"Input01": "1", "SerialNo": "0", "DeviceState": "1"})
