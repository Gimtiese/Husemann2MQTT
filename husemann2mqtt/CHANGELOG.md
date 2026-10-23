# Changelog

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
