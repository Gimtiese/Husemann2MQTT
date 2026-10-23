"""Ingress-Webserver: Seite, JSON-API, Zugriffsschutz."""

from __future__ import annotations

import http.client
import json

import pytest

from app.registry import Registry, device_key
from app.web import WebServer


@pytest.fixture
def server(tmp_path, d11, d99):
    reg = Registry(str(tmp_path / "devices.json"))
    reg.observe_gateway("Gateway 1", "192.0.2.10", True, 10.0, 2)
    reg.observe_devices("Gateway 1", {"D11": d11, "D99": d99})
    web = WebServer(reg, 0, allow_any_client=True)      # Port 0 = freier Port
    web.start()
    yield web
    web.stop()


def request(web: WebServer, method: str, path: str, body: dict | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", web.bound_port, timeout=5)
    headers = {"Content-Type": "application/json"} if body is not None else {}
    conn.request(method, path, json.dumps(body) if body is not None else None, headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, resp.getheader("Content-Type", ""), data


def test_index_page(server):
    status, ctype, body = request(server, "GET", "/")
    assert status == 200 and ctype.startswith("text/html")
    assert b"Husemann2MQTT" in body and b'fetch("api/devices"' not in body or b"api/devices" in body
    assert request(server, "GET", "/index.html")[0] == 200
    assert request(server, "HEAD", "/")[0] == 200
    assert request(server, "GET", "/gibtsnicht")[0] == 404


def test_devices_api_roundtrip(server):
    status, ctype, body = request(server, "GET", "/api/devices?x=1")
    assert status == 200 and ctype.startswith("application/json")
    snap = json.loads(body)
    assert [d["node"] for d in snap["devices"]] == ["D11", "D99"]
    assert snap["devices"][1]["summary"] == "Eingänge an: 1"
    assert snap["devices"][0]["values"][0]["value"] == "-35 kW"
    assert snap["gateways"][0]["name"] == "Gateway 1" and snap["pending"] is False

    key = device_key("Gateway 1", "D11")
    status, _, body = request(server, "POST", "/api/devices",
                              {"devices": [{"key": key, "name": "Hauptzähler", "enabled": True}, "müll"]})
    assert status == 200
    out = json.loads(body)
    assert out["changed"] == 1 and out["pending"] is True
    d11 = next(d for d in out["devices"] if d["key"] == key)
    assert d11["name"] == "Hauptzähler" and d11["enabled"] is True and d11["display_name"] == "Hauptzähler"
    assert server.registry.changed.is_set()


def test_forget_api(server):
    key = device_key("Gateway 1", "D99")
    status, _, body = request(server, "POST", "/api/forget", {"key": key})
    assert status == 200 and json.loads(body)["forgotten"] is True
    assert [d["node"] for d in json.loads(body)["devices"]] == ["D11"]
    assert request(server, "POST", "/api/forget", {"key": key})[0] == 404


def test_bad_requests(server):
    assert request(server, "POST", "/api/devices", {"devices": "x"})[0] == 400
    assert request(server, "POST", "/api/devices", [1, 2])[0] == 400
    conn = http.client.HTTPConnection("127.0.0.1", server.bound_port, timeout=5)
    conn.request("POST", "/api/devices", b"{kaputt", {"Content-Type": "application/json"})
    assert conn.getresponse().status == 400
    conn.close()
    assert request(server, "POST", "/nix", {})[0] == 404


def test_only_ingress_proxy_allowed(tmp_path):
    web = WebServer(Registry(str(tmp_path / "d.json")), 0, allow_any_client=False)
    web.start()
    try:
        status, _, body = request(web, "GET", "/api/devices")
        assert status == 403 and b"Ingress" in body
    finally:
        web.stop()
