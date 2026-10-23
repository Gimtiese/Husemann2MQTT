# Husemann2MQTT

Home-Assistant-Add-on, das ein **Husemann Smart Panel** (Tixi.Gate-Datenlogger
mit SpiderControl-HMI) über die **TiXML-HTTP-API** ausliest und alle Zähler und
I/O-Module per **MQTT-Discovery** in Home Assistant anlegt.

Keine Modbus-Verkabelung, kein Login nötig: Der Logger stellt die Live-Werte
aller am Bus konfigurierten Geräte als XML bereit. Das Add-on pollt sie und
legt automatisch Sensoren an – welche Geräte erscheinen, ergibt sich aus der
Konfiguration des jeweiligen Panels.

## Installation (über GitHub in Home Assistant OS)

1. In Home Assistant: **Einstellungen → Add-ons → Add-on Store**.
2. Oben rechts **⋮ → Repositories**.
3. URL dieses Repos eintragen:
   `https://github.com/Gimtiese/Husemann2MQTT`
4. Das Add-on **Husemann2MQTT** erscheint im Store → **Installieren**.
5. Unter **Konfiguration** die Gateways (und ggf. MQTT) einstellen, dann **Starten**.

> Voraussetzung: Ein MQTT-Broker in HA (z. B. das **Mosquitto**-Add-on) und die
> **MQTT-Integration**. Das Add-on nutzt dessen Zugangsdaten automatisch; ein
> externer Broker lässt sich alternativ in den Optionen eintragen.

## Was wird erkannt

Jeder Busteilnehmer des Loggers (`D11`, `D12`, …) wird automatisch zu einem
HA-Gerät, der Gerätetyp kommt aus der Panel-Konfiguration. Getestet mit:

| Gerätetyp | Hersteller | Inhalt |
|-----------|------------|--------|
| EM330 / EM340 | Carlo Gavazzi | 3-Phasen-Energiezähler (U, I, P, Q, S, cos φ, f, kWh) |
| MRTP I/O-Modul | Tixi | Digitale Eingänge + Relais (nur lesen) |

Andere Zähler, die der Logger über Modbus bereitstellt, werden ebenfalls
angelegt; Einheiten werden per Namensregel aus den Datenpunktnamen abgeleitet.
Details zum Datenmodell: [`docs/DATAMODEL.md`](docs/DATAMODEL.md).

## Terminal-UI (ohne Home Assistant)

Für Exploration und Verläufe direkt am Rechner: [`tui/`](tui/README.md) –
live Übersicht, Diagramme und Ereignisse, mit lokaler Aufzeichnung (SQLite).

## Konfiguration (Auszug)

```yaml
gateways:
  - name: Gateway 1
    host: 192.168.0.1
    port: 8080
poll_interval: 10          # Sekunden
base_topic: husemann
discovery_prefix: homeassistant
include_devices: []        # leer = alle; sonst z. B. ["D11","EM340"]
exclude_devices: []        # z. B. ["D99"]
device_names:              # eigene Anzeigenamen in Home Assistant
  - node: D11
    name: Hauptzähler
mqtt:                      # leer lassen, wenn HA-Mosquitto genutzt wird
  host: ""
  port: 1883
  username: ""
  password: ""
```

Alle Optionen: [`husemann2mqtt/DOCS.md`](husemann2mqtt/DOCS.md).

## Status

v0.2.0 – liest beliebig viele Gateways, benennt Geräte nach Wunsch und
publiziert read-only nach MQTT. Schalten der Relais ist bewusst (noch) nicht aktiv.

## Lizenz

MIT – siehe [LICENSE](LICENSE).
