# Changelog

## 0.4.0

- **Geänderte State-Topics:** Geräte mit eigenem Namen liegen jetzt unter
  `husemann/<gateway>/<Gerätename>/<Punkt>` statt `husemann/<gateway>/<Node-ID>/<Punkt>`
  (Namen slugifiziert; ohne Namen weiterhin die Node-ID).
- **Upgrade-Hinweis:** Home Assistant braucht nichts – `unique_id`, `object_id`
  und `device.identifiers` hängen weiterhin an der Node-ID, die Discovery wird beim
  Start mit dem neuen `state_topic` neu gesendet. Entitäten, Historie und
  Zuordnung bleiben erhalten. **Externe Konsumenten** der alten Topics
  (Node-RED, MQTT-Trigger) müssen auf die neuen Topics umstellen oder die neue
  Option `topic_naming: node` setzen, dann bleibt alles beim Alten.
- Umbenennen in der Oberfläche sendet die Discovery sofort mit dem neuen Topic neu.
- Namenskollisionen: Zwei Geräte mit gleichem Namen auf einem Gateway bekommen
  keine gemeinsamen Topics (Ausweichen auf die Node-ID).
- Neue Option `topic_naming` (`name`, Standard | `node`): Topics nach GUI-Name
  oder – wie bisher – nach Node-ID.

## 0.3.1

- Seitenleiste zeigt je Gerät die aktuellen Messwerte: Kurzfassung (Leistung,
  Spannung, Energie bzw. Eingänge/Relais) in der Liste und alle Werte per
  „Werte“ aufklappbar – zum Zuordnen beim Benennen.

## 0.3.0

- Weboberfläche in der HA-Seitenleiste (Ingress): gefundene Geräte mit Typ,
  Seriennummer und Status ansehen, benennen und per Haken für MQTT auswählen.
  Änderungen wirken ohne Neustart; abgewählte Geräte werden aus HA entfernt.
- Auswahl und Namen liegen persistent in `/data/devices.json`.
- Neue Option `auto_enable_new_devices` (Standard `false`): neue Geräte erst
  nach Auswahl publizieren. `include_devices`, `exclude_devices` und
  `device_names` sind jetzt Vorgaben für neu gefundene Geräte.

## 0.2.0

- Neue Option `device_names`: erkannte Geräte bekommen in Home Assistant eigene
  Anzeigenamen (optional je Gateway). Entity-IDs bleiben dabei stabil.
- Entitäten heißen jetzt `<Gerätename> <Datenpunkt>` (`has_entity_name`).
- Jedes Gateway erscheint als eigenes HA-Gerät mit Diagnose-Entitäten
  **Online**, **Antwortzeit** und **Geräte**; die Zähler sind ihm untergeordnet.
- Hersteller wird aus dem Gerätetyp abgeleitet, Seriennummer wird übernommen.
- Gerätetypen werden nachgeladen, falls das Gateway beim Start nicht erreichbar war.

## 0.1.0

- Erste Version: liest alle Gateways/Geräte per TiXML und publiziert read-only
  nach MQTT (Discovery).
