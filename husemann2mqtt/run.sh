#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
set -e

export LOG_LEVEL="$(bashio::config 'log_level' 'info')"

# MQTT-Zugangsdaten des HA-Brokers (services: mqtt) in Umgebungsvariablen legen,
# falls der Dienst verfügbar ist. Die App nutzt sie, wenn keine externen Daten
# unter der Option `mqtt` gesetzt sind.
if bashio::services.available "mqtt"; then
  export HA_MQTT_HOST="$(bashio::services 'mqtt' 'host')"
  export HA_MQTT_PORT="$(bashio::services 'mqtt' 'port')"
  export HA_MQTT_USER="$(bashio::services 'mqtt' 'username')"
  export HA_MQTT_PASSWORD="$(bashio::services 'mqtt' 'password')"
  export HA_MQTT_SSL="$(bashio::services 'mqtt' 'ssl')"
  bashio::log.info "MQTT-Dienst von Home Assistant gefunden: ${HA_MQTT_HOST}:${HA_MQTT_PORT}"
else
  bashio::log.info "Kein HA-MQTT-Dienst – es werden die externen Optionen unter 'mqtt' verwendet."
fi

bashio::log.info "Starte Husemann2MQTT …"
exec python3 -u -m app.main
