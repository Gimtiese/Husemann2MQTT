"""TiXML-Client: Parsen von Prozessbaum und Gerätekonfiguration, Fehlerfälle."""

from __future__ import annotations

import requests

from app.mapping import meta_for, pretty_name


def test_read_process_with_models(gateway):
    assert gateway.load_models() == {"D11": "EM330", "D99": "MRTP_XX"}
    devices = gateway.read_process()
    assert set(devices) == {"D11", "D99"}
    assert devices["D11"].model == "EM330" and devices["D11"].values["Frequency"] == "49.9"
    assert devices["D99"].values == {"Input01": "0", "SerialNo": "0", "DeviceState": "1"}
    assert gateway.last_latency_ms is not None and gateway.last_latency_ms >= 0


def test_models_loaded_lazily(gateway):
    # load_models() nicht aufgerufen -> read_process() holt die Typen nach
    assert gateway.read_process()["D11"].model == "EM330"


def test_http_error_returns_empty(gateway):
    def fail(url, timeout):
        raise requests.RequestException("boom")

    gateway._session.get = fail
    assert gateway.read_process() == {} and gateway.last_latency_ms is None
    assert gateway.load_models() == {}


def test_invalid_xml_returns_empty(gateway):
    class Resp:
        content = b"<kaputt"

        def raise_for_status(self):
            pass

    gateway._session.get = lambda url, timeout: Resp()
    assert gateway.read_process() == {}


def test_mapping_rules():
    assert meta_for("RealPower_L1_N").unit == "kW" and meta_for("RealPower_L1_N").device_class == "power"
    assert meta_for("RealEnergy_L1L2L3Con").state_class == "total_increasing"
    assert meta_for("ReactiveEnergy_L1L3i").device_class is None            # kvarh ohne HA-Klasse
    assert meta_for("Input03").component == "binary_sensor"
    assert meta_for("DeviceState").diagnostic and meta_for("DeviceState").component == "binary_sensor"
    assert meta_for("Irgendwas").component == "sensor" and meta_for("Irgendwas").unit is None
    assert pretty_name("RealPower_L1_N") == "Real Power L1 N"
