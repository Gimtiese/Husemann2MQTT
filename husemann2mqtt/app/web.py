"""Ingress-Weboberfläche: gefundene Geräte ansehen, benennen und für MQTT auswählen.

Bewusst nur Python-Bordmittel (``http.server``), eine statische HTML-Seite und eine
kleine JSON-API. Die Seite wird von Home Assistant per Ingress eingebettet; Anfragen
kommen dann ausschließlich vom Supervisor-Proxy (172.30.32.2). Alle anderen Quellen
werden abgewiesen, sofern nicht ``allow_any_client`` gesetzt ist (nur für Tests).
"""

from __future__ import annotations

import json
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources

from .registry import Registry

_LOGGER = logging.getLogger(__name__)

INGRESS_PROXY = "172.30.32.2"
MAX_BODY = 256 * 1024


def _load_index() -> bytes:
    return resources.files(__package__).joinpath("static/index.html").read_bytes()


class WebServer:
    def __init__(self, registry: Registry, port: int, allow_any_client: bool = False):
        self.registry = registry
        self.port = port
        self.allow_any_client = allow_any_client
        self._index = _load_index()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        handler = self._make_handler()
        self._server = ThreadingHTTPServer(("0.0.0.0", self.port), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name="web", daemon=True)
        self._thread.start()
        _LOGGER.info("Weboberfläche (Ingress) auf Port %d bereit", self.port)

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()

    @property
    def bound_port(self) -> int:
        return self._server.server_address[1] if self._server else self.port

    # ------------------------------------------------------------------ #
    def _make_handler(self):
        web = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "Husemann2MQTT"
            protocol_version = "HTTP/1.1"

            # --- Hilfen -------------------------------------------------
            def log_message(self, fmt, *args):  # noqa: ANN001 - Signatur der Basisklasse
                _LOGGER.debug("web %s: " + fmt, self.client_address[0], *args)

            def _authorized(self) -> bool:
                if web.allow_any_client or self.client_address[0] == INGRESS_PROXY:
                    return True
                self._send(HTTPStatus.FORBIDDEN, b"Nur ueber Home Assistant (Ingress) erreichbar.\n",
                           "text/plain; charset=utf-8")
                return False

            def _send(self, status: HTTPStatus, body: bytes, ctype: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _json(self, data: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
                self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")

            def _read_json(self) -> dict | None:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > MAX_BODY:
                    self._json({"error": "Ungültige Anfrage"}, HTTPStatus.BAD_REQUEST)
                    return None
                try:
                    data = json.loads(self.rfile.read(length).decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    self._json({"error": "Kein gültiges JSON"}, HTTPStatus.BAD_REQUEST)
                    return None
                if not isinstance(data, dict):
                    self._json({"error": "Objekt erwartet"}, HTTPStatus.BAD_REQUEST)
                    return None
                return data

            def _route(self) -> str:
                # Ingress reicht den Pfad relativ zum Add-on durch; Query-String ignorieren.
                return self.path.split("?", 1)[0].rstrip("/") or "/"

            # --- Routen -------------------------------------------------
            def do_GET(self) -> None:  # noqa: N802
                if not self._authorized():
                    return
                route = self._route()
                if route in ("/", "/index.html"):
                    self._send(HTTPStatus.OK, web._index, "text/html; charset=utf-8")
                elif route == "/api/devices":
                    self._json(web.registry.snapshot())
                elif route == "/api/health":
                    self._json({"ok": True})
                else:
                    self._send(HTTPStatus.NOT_FOUND, b"Nicht gefunden\n", "text/plain; charset=utf-8")

            do_HEAD = do_GET  # noqa: N815

            def do_POST(self) -> None:  # noqa: N802
                if not self._authorized():
                    return
                route = self._route()
                data = self._read_json()
                if data is None:
                    return
                if route == "/api/devices":
                    changes = data.get("devices")
                    if not isinstance(changes, list):
                        self._json({"error": "'devices' (Liste) fehlt"}, HTTPStatus.BAD_REQUEST)
                        return
                    changed = web.registry.update([c for c in changes if isinstance(c, dict)])
                    _LOGGER.info("Weboberfläche: %d Geräte geändert", changed)
                    self._json({"changed": changed, **web.registry.snapshot()})
                elif route == "/api/forget":
                    key = str(data.get("key", ""))
                    ok = web.registry.forget(key)
                    self._json({"forgotten": ok, **web.registry.snapshot()},
                               HTTPStatus.OK if ok else HTTPStatus.NOT_FOUND)
                else:
                    self._send(HTTPStatus.NOT_FOUND, b"Nicht gefunden\n", "text/plain; charset=utf-8")

        return Handler
