#!/bin/sh
set -eu

CONFIG_PATH="${EASY_PROTOCOL_CONFIG_PATH:-/etc/easy-protocol/config.yaml}"
RUNTIME_ENV_PATH="${EASY_PROTOCOL_RUNTIME_ENV_PATH:-/etc/easy-protocol/runtime.env}"
BOOTSTRAP_PATH="${EASY_PROTOCOL_BOOTSTRAP_PATH:-/etc/easy-protocol/bootstrap/r2-bootstrap.json}"
IMPORT_CODE="${EASY_PROTOCOL_IMPORT_CODE:-}"
STATE_DIR="${EASY_PROTOCOL_STATE_DIR:-/var/lib/easy-protocol}"
IMPORT_STATE_PATH="${EASY_PROTOCOL_IMPORT_STATE_PATH:-${STATE_DIR}/import-sync-state.json}"
SYNC_FLAG_PATH="${EASY_PROTOCOL_IMPORT_SYNC_FLAG_PATH:-${STATE_DIR}/import-sync.restart}"
RESET_STORE_ON_BOOT="${EASY_PROTOCOL_RESET_STORE_ON_BOOT:-false}"

mkdir -p "$(dirname "$CONFIG_PATH")" "$(dirname "$RUNTIME_ENV_PATH")" "$STATE_DIR"

if [ ! -f "$BOOTSTRAP_PATH" ] && [ -n "$IMPORT_CODE" ]; then
  mkdir -p "$(dirname "$BOOTSTRAP_PATH")"
  echo "[easy-protocol] import code provided, generating bootstrap file at $BOOTSTRAP_PATH"
  python /usr/local/bin/easyprotocol-import-code.py inspect \
    --import-code "$IMPORT_CODE" \
    --output "$BOOTSTRAP_PATH"
fi

if [ ! -f "$CONFIG_PATH" ] && [ -f "$BOOTSTRAP_PATH" ]; then
  echo "[easy-protocol] runtime config missing, attempting bootstrap via $BOOTSTRAP_PATH"
  python /usr/local/bin/bootstrap-service-config.py \
    --bootstrap-path "$BOOTSTRAP_PATH" \
    --config-path "$CONFIG_PATH" \
    --runtime-env-path "$RUNTIME_ENV_PATH" \
    --state-path "$IMPORT_STATE_PATH"
fi

if [ ! -f "$CONFIG_PATH" ]; then
  cp /opt/easy-protocol/config.template.yaml "$CONFIG_PATH"
  echo "[easy-protocol] generated default config at $CONFIG_PATH"
fi

if [ -f "$RUNTIME_ENV_PATH" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$RUNTIME_ENV_PATH"
  set +a
  RESET_STORE_ON_BOOT="${EASY_PROTOCOL_RESET_STORE_ON_BOOT:-$RESET_STORE_ON_BOOT}"
fi

case "$(echo "$RESET_STORE_ON_BOOT" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on)
    echo "[easy-protocol] EASY_PROTOCOL_RESET_STORE_ON_BOOT=true -> clearing $STATE_DIR"
    rm -rf "${STATE_DIR:?}"/*
    ;;
  *)
    ;;
esac

resolve_bootstrap_sync_setting() {
  python - "$BOOTSTRAP_PATH" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
if not path.exists():
    print("false")
    print("7200")
    raise SystemExit(0)

payload = json.loads(path.read_text(encoding="utf-8-sig"))
print("true" if payload.get("syncEnabled", True) else "false")
print(int(payload.get("syncIntervalSeconds") or 7200))
PY
}

start_runtime() {
  if [ -f "$RUNTIME_ENV_PATH" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$RUNTIME_ENV_PATH"
    set +a
  fi

  if [ "$(id -u)" = "0" ]; then
    chown -R easy:easy "$STATE_DIR" "$(dirname "$CONFIG_PATH")" /opt/easy-protocol
    gosu easy /usr/local/bin/easy_protocol &
  else
    /usr/local/bin/easy_protocol &
  fi

  APP_PID=$!
}

start_sync_loop() {
  SYNC_INTERVAL_SECONDS="$1"
  (
    while true; do
      sleep "$SYNC_INTERVAL_SECONDS"
      python /usr/local/bin/bootstrap-service-config.py \
        --bootstrap-path "$BOOTSTRAP_PATH" \
        --config-path "$CONFIG_PATH" \
        --runtime-env-path "$RUNTIME_ENV_PATH" \
        --state-path "$IMPORT_STATE_PATH" \
        --mode sync \
        --updated-flag-path "$SYNC_FLAG_PATH"
      if [ -f "$SYNC_FLAG_PATH" ]; then
        echo "[easy-protocol] remote runtime config updated, restarting service"
        kill "$APP_PID" 2>/dev/null || true
        break
      fi
    done
  ) &
  SYNC_PID=$!
}

SYNC_ENABLED="false"
SYNC_INTERVAL_SECONDS="7200"
if [ -f "$BOOTSTRAP_PATH" ]; then
  SYNC_VALUES="$(resolve_bootstrap_sync_setting)"
  SYNC_ENABLED="$(printf '%s' "$SYNC_VALUES" | sed -n '1p')"
  SYNC_INTERVAL_SECONDS="$(printf '%s' "$SYNC_VALUES" | sed -n '2p')"
fi

while true; do
  rm -f "$SYNC_FLAG_PATH"
  start_runtime
  if [ "$SYNC_ENABLED" = "true" ] && [ -f "$BOOTSTRAP_PATH" ]; then
    start_sync_loop "$SYNC_INTERVAL_SECONDS"
  else
    SYNC_PID=""
  fi

  APP_STATUS=0
  wait "$APP_PID" || APP_STATUS=$?

  if [ -n "${SYNC_PID:-}" ]; then
    kill "$SYNC_PID" 2>/dev/null || true
    wait "$SYNC_PID" 2>/dev/null || true
  fi

  if [ -f "$SYNC_FLAG_PATH" ]; then
    rm -f "$SYNC_FLAG_PATH"
    continue
  fi

  exit "$APP_STATUS"
done
