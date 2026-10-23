# husemann-tui

Terminal-UI zum Erkunden und Aufzeichnen der Daten eines Husemann Smart Panel
(Tixi.Gate HE653, TiXML-HTTP-API). **Läuft komplett unabhängig vom HA-Add-on** –
braucht weder Home Assistant noch MQTT, nur Netzzugriff auf das Gateway.

## Installation

```bash
cd tui
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/husemann-tui            # oder: pipx install ./tui
```

## Benutzung

```bash
husemann-tui -g 192.168.0.1                    # ein Gateway (Port 8080)
husemann-tui -g "Gateway 1=192.168.0.1" -g "Gateway 2=192.168.0.2:8080"
husemann-tui init-config                       # Konfig nach ~/.config/husemann-tui/config.toml, danach reicht:
husemann-tui
husemann-tui record                            # nur aufzeichnen (ohne UI, z. B. als Dienst)
```

Optionen: `-i/--interval` (Sek., Standard 2), `--db`, `--retention-days` (Standard 30), `--no-record`.

### Tabs & Tasten

| Tab | Inhalt |
|-----|--------|
| **Übersicht** | Baum Gateway → Geräte; je Gerät alle Datenpunkte live mit Min/Max seit Start und Trend-Sparkline |
| **Verlauf** | Diagramm aus der Datenbank; Enter in der Übersicht öffnet den Punkt, `p` heftet bis zu 4 Reihen zum Vergleich an, `c` leert. Zwei verschiedene Einheiten bekommen eine linke und eine rechte Achse, Schaltzustände werden als Treppe gezeichnet, Ausfälle des Gateways bleiben als Lücke sichtbar |
| **Ereignisse** | Schaltzustände (Eingänge/Relais/DeviceState) und Gateway online/offline |

`1`–`6` Zeitraum (5 Min · 1 Std · 6 Std · 24 Std · 7 Tage · 30 Tage), `q` beenden.

## Aufzeichnung

SQLite unter `~/.local/share/husemann-tui/history.db`. Werte werden nur bei
Änderung gespeichert (plus Heartbeat alle 60 s), alte Daten nach `retention_days`
automatisch gelöscht. Die Aufzeichnung läuft nur, solange die TUI oder
`husemann-tui record` läuft.

## Hinweis

`husemann_tui/mapping.py` ist eine Kopie von `husemann2mqtt/app/mapping.py`
(Einheiten/Klassen der Datenpunkte) – bei Änderungen beide anpassen.
