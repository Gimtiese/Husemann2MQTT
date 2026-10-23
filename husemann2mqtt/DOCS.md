# Husemann2MQTT – Konfiguration

Dieses Add-on pollt eine oder mehrere Tixi.Gate-Datenlogger (das Herz eines
Husemann Smart Panel) über deren TiXML-HTTP-API
(`http://<host>:<port>/System/Properties/Process/`) und veröffentlicht alle
gefundenen Geräte und Datenpunkte per MQTT-Discovery in Home Assistant.

Welche Geräte erscheinen, hängt allein von der Konfiguration des Panels ab:
Jeder Busteilnehmer (`D11`, `D12`, …) wird automatisch erkannt, sein Typ
(z. B. `EM330`, `EM340`, `MRTP_XX`) aus der Gerätekonfiguration gelesen.

## Geräte auswählen und benennen (Seitenleiste)

Nach dem Start erscheint **Husemann2MQTT** in der Seitenleiste von Home
Assistant. Die Seite zeigt alle gefundenen Geräte je Gateway mit Knoten, Typ,
Seriennummer, Status und Anzahl der Messwerte.

1. Haken bei den Geräten setzen, die in Home Assistant erscheinen sollen.
2. Optional einen Namen vergeben (leer = Standardname wie `EM340 D15 (Gateway 1)`).
3. **Speichern**. Das Add-on legt die Geräte innerhalb weniger Sekunden per
   MQTT-Discovery an – mit dem vergebenen Namen. Abgehakte Geräte werden samt
   Entitäten aus Home Assistant entfernt.

Die Auswahl liegt in `/data/devices.json` des Add-ons und überlebt Neustarts
und Updates. Geräte, die nicht mehr am Bus gefunden werden, lassen sich über
**Vergessen** aus Liste und Home Assistant entfernen.

Die Entity-IDs (`sensor.husemann_gateway_1_d11_…`) bleiben beim Umbenennen
stabil, Verläufe und Dashboards gehen nicht verloren. Wurde ein Gerät bereits
manuell in Home Assistant umbenannt, hat dieser Name dort Vorrang.

## Optionen

### `gateways` (Liste, Pflicht)

Die auszulesenden Tixi-Gateways.

| Feld | Typ | Beschreibung |
|------|-----|--------------|
| `name` | Text | Anzeigename; wird Teil der Topics, Entity-IDs und Gerätenamen |
| `host` | Text | IP-Adresse oder Hostname der Tixi.Gate |
| `port` | Port | Port der TiXML-API (Werkseinstellung der HE653: `8080`) |

```yaml
gateways:
  - name: Gateway 1
    host: 192.168.0.1
    port: 8080
  - name: Gateway 2
    host: 192.168.0.2
    port: 8080
```

> Den `name` nach der Inbetriebnahme möglichst nicht mehr ändern – er steckt in
> den Entity-IDs. Für sprechende Anzeigenamen gibt es `device_names`.

### `poll_interval` (Sekunden, Standard 10)

Abfrageintervall pro Gateway. Der Logger pollt seine Zähler intern meist mit
1 s; 10 s sind für Home Assistant in der Regel ausreichend.

### `request_timeout` (Sekunden, Standard 8)

HTTP-Timeout je Anfrage.

### `base_topic` (Standard `husemann`)

Präfix der State-Topics: `husemann/<gateway>/<device>/<punkt>`.

### `discovery_prefix` (Standard `homeassistant`)

Muss zum Discovery-Präfix der MQTT-Integration passen.

### `auto_enable_new_devices` (Standard `false`)

`false`: Neu gefundene Geräte erscheinen erst in Home Assistant, wenn sie in
der Seitenleiste angehakt wurden (empfohlen, wenn man nur einen Teil der
Zähler braucht oder erst Namen vergeben will).

`true`: Neu gefundene Geräte werden sofort publiziert, sofern
`include_devices`/`exclude_devices` sie zulassen. Die Auswahl lässt sich
danach jederzeit in der Seitenleiste ändern.

### `include_devices` / `exclude_devices` (Listen)

Vorgabe für **neu gefundene** Geräte bei `auto_enable_new_devices: true`.
Matcht **Knotenname** (`D11`) oder **Gerätetyp** (`EM340`),
Groß-/Kleinschreibung egal.

- `include_devices` leer → **alle** Geräte.
- `include_devices` gesetzt → nur diese (Whitelist).
- `exclude_devices` → diese immer weglassen (hat Vorrang).

```yaml
include_devices: ["EM340"]     # nur die EM340-Zähler
exclude_devices: ["D99"]       # I/O-Modul auslassen
```

Bereits bekannte Geräte werden davon nicht mehr berührt; dafür ist die
Seitenleiste zuständig.

### `device_names` (Liste, optional)

Vorgabe-Namen für **neu gefundene** Geräte, z. B. um eine Konfiguration auf
eine zweite Instanz zu übertragen. In der Seitenleiste vergebene Namen haben
Vorrang. Ohne Eintrag heißt ein Gerät z. B. `EM340 D15 (Gateway 1)`.

| Feld | Pflicht | Beschreibung |
|------|---------|--------------|
| `node` | ja | Knoten des Geräts, z. B. `D15` |
| `name` | ja | Anzeigename in Home Assistant |
| `gateway` | nein | Nur für dieses Gateway (`name` aus `gateways`); leer = alle |

```yaml
device_names:
  - node: D11
    name: Hauptzähler
  - node: D15
    name: Wärmepumpe
  - node: D99
    name: Schaltmodul
    gateway: Gateway 2
```

Die Entitäten heißen dann `Hauptzähler Real Power L1 N` usw.

### `mqtt` (optional)

Nur nötig für einen **externen** Broker. Bleibt `host` leer, nutzt das Add-on
automatisch den in Home Assistant installierten MQTT-Dienst (z. B. Mosquitto-Add-on).

| Feld | Beschreibung |
|------|--------------|
| `host` | Broker-Host (leer = HA-Dienst) |
| `port` | Standard 1883 |
| `username` / `password` | Zugangsdaten |
| `tls` | TLS aktivieren (bool) |

### `log_level`

`trace` · `debug` · `info` · `notice` · `warning` · `error` · `fatal`.

## Entitäten

- Pro Gateway ein HA-Gerät (`Gateway 1`) mit Diagnose-Entitäten
  **Online**, **Antwortzeit** und **Geräte** (Anzahl publizierter Busteilnehmer).
- Pro Busteilnehmer ein HA-Gerät, dem Gateway untergeordnet; Seriennummer
  wird übernommen, sofern der Zähler sie liefert.
- Messwerte als `sensor` mit passender `device_class`/Einheit
  (V, A, kW, kVA, kvar, Hz, kWh, kvarh, power_factor).
- `Input0x`/`SetRelais0x`/`DeviceState` als `binary_sensor`.
- Energiezähler mit `state_class: total_increasing` (für das HA-Energie-Dashboard).
- Verfügbarkeit aller Entitäten über `husemann/bridge/status` (LWT).
- Abgewählte Geräte werden per leerem, retained Discovery-Payload aus HA entfernt.

## Hinweise

- Die Einheiten werden per Namensregel aus den Datenpunktnamen abgeleitet
  (`app/mapping.py`). Die kW/kVA-Skalierung der Leistungswerte bei der ersten
  Inbetriebnahme einmal gegen die Anzeige des Panels prüfen.
- Busteilnehmer, die konfiguriert, aber nicht erreichbar sind, liefern nur
  `DeviceState = 0` und wenige Punkte – sie erscheinen trotzdem, bleiben aber
  ohne Messwerte.
- Das Add-on ist **read-only**: Relais (`SetRelais0x`) werden nur gelesen,
  nicht geschaltet.
