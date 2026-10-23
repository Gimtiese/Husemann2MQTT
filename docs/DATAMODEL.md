# Husemann Smart Panel – Datenmodell

Technische Referenz für die Datenquelle des Add-ons. Ermittelt an einem Panel mit
**Tixi.Gate HE653** (Firmware 5.x) und SpiderControl-HMI; andere Panels derselben
Bauart verhalten sich gleich, die Geräteliste unterscheidet sich je nach Anlage.

## Dienste des Loggers

| Port | Dienst | Nutzung |
|------|--------|---------|
| `:80` | SpiderControl-Web-HMI | Visualisierung des Herstellers |
| `:502` | Modbus-TCP | vorhanden, in der Praxis unzuverlässig |
| `:8080` | **TiXML-HTTP-API** | **Datenquelle des Add-ons** |

## Zugriff auf die Daten (kein Login nötig)

Die Tixi stellt ihren internen Objektbaum als XML per HTTP-GET bereit:

```
GET http://<host>:8080/System/Properties/Process/        → Live-Prozesswerte
GET http://<host>:8080/System/Config/PROCCFG/External/   → Bus-/Gerätekonfiguration
GET http://<host>:8080/System/Config/                    → Gesamtkonfig (~200 KB)
```

Werte stehen im Attribut `_`:  `<Frequency _="49.9"/>` → Wert = `49.9`.

(Alternativ existiert die POST-API `POST /cgi-bin/TiXML.cgi?100` mit
`Content-Type: text/xml`. Für reines Lesen ist der virtuelle GET-Pfad einfacher
und ausreichend.)

## Struktur des Prozessbaums

```
<Process>
  <Modbus>
    <D11> … </D11>   ← ein Gerät je Dxx-Knoten
    …
    <D99> … </D99>
  </Modbus>
  <MB>  … </MB>      ← M-Bus (falls Zähler konfiguriert)
  <IO>  … </IO>      ← lokales I/O des Loggers
  <PV>/<IW>/<ID>/<Program>/<VPN>/<IBMConn> … ← System-/Programmvariablen
</Process>
```

## Geräteinventar

Die Busteilnehmer (RS485/Modbus, vom Logger mit ca. 1 s gepollt) stehen in
`System/Config/PROCCFG/External/`:

```xml
<Device _="11" NameUser="EM330" …>
```

`_` ist die Knotennummer (→ `D11`), `NameUser` der Gerätetyp. Das Add-on nutzt
beides für Gerätename, Hersteller und Filter (`include_devices`/`exclude_devices`).

Typische Belegung eines Panels (Beispiel):

| Knoten | Typ (`NameUser`) | Beschreibung |
|--------|------------------|--------------|
| D11 … | **EM330** (Carlo Gavazzi) | 3-Phasen-Energiezähler |
| D15 … | **EM340** (Carlo Gavazzi) | 3-Phasen-Energiezähler |
| D99 | **MRTP_XX** (Tixi) | I/O-Modul: digitale Eingänge + Relais |

### Datenpunkte Energiezähler (EM330/EM340)

| Datenpunkt | Einheit | HA device_class | Hinweis |
|------------|---------|-----------------|---------|
| `Voltage_L1_N`, `L2_N`, `L3_N` | V | voltage | Phase-Neutral |
| `Voltage_L1_L2`, `L2_L3`, `L3_L1` | V | voltage | Phase-Phase |
| `ApparentCurrentL1/L2/L3` | A | current | |
| `RealPower_L1_N/L2_N/L3_N` | kW | power | Werte ≈ U·I/1000 ⇒ **kW** |
| `RealPowerPSum_P1P2P3` | kW | power | Summe |
| `ApparentPower_L1_N/L2_N/L3_N` | kVA | apparent_power | |
| `ApparentPwSSm_P1P2P3` | kVA | apparent_power | Summe |
| `ReactivePower_L1/L2/L3` | kvar | reactive_power | |
| `ReactivePwrQs1_2_3` | kvar | reactive_power | Summe |
| `CosPhi_UL1_IL1/UL2_IL2/UL3_IL3` | – | power_factor | |
| `Frequency` | Hz | frequency | |
| `RealEnergy_L1L2L3Con` | kWh | energy | **total_increasing** (bezogen) |
| `RealEnergy_L1L2L3Del` | kWh | energy | **total_increasing** (geliefert) |
| `ReactiveEnergy_L1L3i` | kvarh | – | induktiv |
| `ReactiveEnergy_L1L3c` | kvarh | – | kapazitiv |
| `DeviceState` | – | connectivity | Diagnose (1 = online) |
| `SerialNo` | – | – | Diagnose, wird als Seriennummer des HA-Geräts übernommen |
| `ChangeToggle` | – | – | Diagnose |

> ⚠️ Die kW/kVA-Skalierung wurde aus dem Verhältnis der Live-Werte (U·I ≈ P·1000)
> abgeleitet, nicht aus einer Herstellerdoku. Bei der ersten Inbetriebnahme gegen
> die Anzeige des Panels oder ein Referenzmessgerät prüfen und ggf. die Regeln in
> `app/mapping.py` anpassen.

### Datenpunkte I/O-Modul (MRTP_XX)

| Datenpunkt | Typ | HA |
|------------|-----|-----|
| `Input01` … `Input06` | 0/1 | binary_sensor |
| `SetRelais01` … `SetRelais04` | 0/1 | binary_sensor (read-only; Schalten später optional) |
| `DeviceState`, `ChangeToggle` | – | Diagnose |

## Konsequenzen fürs Add-on

- Datenquelle = **TiXML-GET** auf `/System/Properties/Process/`. Kein Modbus, kein Login.
- Geräte = alle `Dxx`-Knoten; per `include_devices`/`exclude_devices` filterbar,
  per `device_names` benennbar.
- Beliebig viele Gateways über die Liste `gateways`.
- Einheiten/Device-Classes per Namensregel (siehe `app/mapping.py`), damit auch
  unbekannte Zählertypen sinnvoll erscheinen.
