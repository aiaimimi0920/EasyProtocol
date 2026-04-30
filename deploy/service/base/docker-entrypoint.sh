#!/bin/sh
set -eu

CONFIG_PATH="${EASY_PROTOCOL_CONFIG_PATH:-/etc/easy-protocol/config.yaml}"
STATE_DIR="${EASY_PROTOCOL_STATE_DIR:-/var/lib/easy-protocol}"
RESET_STORE_ON_BOOT="${EASY_PROTOCOL_RESET_STORE_ON_BOOT:-false}"

mkdir -p "$(dirname "$CONFIG_PATH")" "$STATE_DIR"

if [ ! -f "$CONFIG_PATH" ]; then
  cp /opt/easy-protocol/config.template.yaml "$CONFIG_PATH"
  echo "[easy-protocol] generated default config at $CONFIG_PATH"
fi

case "$(echo "$RESET_STORE_ON_BOOT" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on)
    echo "[easy-protocol] EASY_PROTOCOL_RESET_STORE_ON_BOOT=true -> clearing $STATE_DIR"
    rm -rf "${STATE_DIR:?}"/*
    ;;
  *)
    ;;
esac

if [ "$(id -u)" = "0" ]; then
  chown -R easy:easy "$STATE_DIR" "$(dirname "$CONFIG_PATH")" /opt/easy-protocol
  exec gosu easy /usr/local/bin/easy_protocol
fi

exec /usr/local/bin/easy_protocol
