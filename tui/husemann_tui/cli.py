"""Kommandozeile: ``husemann-tui`` (TUI) und ``husemann-tui record`` (headless)."""

from __future__ import annotations

import argparse
import sys
import time
import tomllib
from pathlib import Path

from .client import GatewayConfig
from .poller import Poller
from .store import default_db_path

CONFIG_PATH = Path.home() / ".config" / "husemann-tui" / "config.toml"

EXAMPLE_CONFIG = """\
# ~/.config/husemann-tui/config.toml
interval = 2            # Poll-Intervall in Sekunden
retention_days = 30     # Verlauf wird danach automatisch gelöscht
# db = "~/.local/share/husemann-tui/history.db"

[[gateway]]
name = "Gateway 1"
host = "192.168.0.1"     # IP der Tixi.Gate
port = 8080              # TiXML-Port (Werkseinstellung 8080)

# [[gateway]]
# name = "Gateway 2"
# host = "192.168.0.2"
"""


def _parse_gateway(spec: str) -> GatewayConfig:
    """``[NAME=]HOST[:PORT]``"""
    name, _, addr = spec.rpartition("=")
    host, _, port = addr.partition(":")
    return GatewayConfig(name or host, host, int(port) if port else 8080)


def _load_config() -> dict:
    try:
        return tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as err:
        sys.exit(f"Konfiguration {CONFIG_PATH} nicht lesbar: {err}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="husemann-tui", description=__doc__)
    ap.add_argument("command", nargs="?", choices=["ui", "record", "init-config"], default="ui",
                    help="ui (Standard) | record (nur aufzeichnen, ohne UI) | init-config")
    ap.add_argument("-g", "--gateway", action="append", metavar="[NAME=]HOST[:PORT]",
                    help="Gateway, mehrfach angebbar (überschreibt die Konfig-Datei)")
    ap.add_argument("-i", "--interval", type=float, help="Poll-Intervall in Sekunden")
    ap.add_argument("--db", type=Path, help="Pfad der Verlaufs-Datenbank")
    ap.add_argument("--retention-days", type=float, help="Aufbewahrung des Verlaufs in Tagen")
    ap.add_argument("--no-record", action="store_true", help="Nichts aufzeichnen")
    args = ap.parse_args()

    if args.command == "init-config":
        if CONFIG_PATH.exists():
            sys.exit(f"{CONFIG_PATH} existiert bereits.")
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(EXAMPLE_CONFIG, encoding="utf-8")
        print(f"Geschrieben: {CONFIG_PATH}")
        return

    cfg = _load_config()
    if args.gateway:
        gateways = [_parse_gateway(g) for g in args.gateway]
    else:
        gateways = [GatewayConfig(g.get("name", g["host"]), g["host"], int(g.get("port", 8080)))
                    for g in cfg.get("gateway", [])]
    if not gateways:
        sys.exit("Kein Gateway angegeben: -g [NAME=]HOST[:PORT] verwenden oder mit "
                 "'husemann-tui init-config' eine Konfiguration anlegen.")
    interval = args.interval or float(cfg.get("interval", 2))
    retention = args.retention_days or float(cfg.get("retention_days", 30))
    db = None if args.no_record else Path(args.db or cfg.get("db") or default_db_path()).expanduser()

    if args.command == "record":
        if db is None:
            sys.exit("record ohne Datenbank ergibt keinen Sinn (--no-record entfernen).")
        n = {"ok": 0, "err": 0}

        def on_result(res):
            n["err" if res.error else "ok"] += 1
            print(f"{time.strftime('%H:%M:%S')} {res.gateway}: "
                  f"{res.error or f'{len(res.devices)} Geräte, {res.latency_ms:.0f} ms'}", flush=True)

        poller = Poller(gateways, interval, db, retention, on_result)
        poller.start()
        print(f"Zeichne auf nach {db} (Strg+C beendet)")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        finally:
            poller.stop()
        return

    from .app import HusemannApp
    HusemannApp(gateways, interval, db, retention).run()
