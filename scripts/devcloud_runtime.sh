#!/usr/bin/env bash
# Manage the Feedback Hub service inside the DevCloud container.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${APP_DIR}/.venv"
RUN_DIR="${APP_DIR}/run"
LOG_DIR="${APP_DIR}/logs"
PID_FILE="${RUN_DIR}/feedback_hub.pid"
APP_PORT="${APP_PORT:-8000}"
VECTOR_PORT="${VECTOR_PORT:-8011}"
VECTOR_RUNTIME="${APP_DIR}/scripts/vector_runtime.sh"
VECTOR_DATA_DIR="${VECTOR_DATA_DIR:-${APP_DIR}/feedback_hub/data/vector_index}"

mkdir -p "$RUN_DIR" "$LOG_DIR" "${APP_DIR}/feedback_hub/data"

# 0=valid active index, 1=no index configured, 2=unsafe/broken metadata.
vector_index_state() {
  if [ -e "${VECTOR_DATA_DIR}/manifest.json" ]; then
    [ -f "${VECTOR_DATA_DIR}/manifest.json" ] || return 2
    python3 -c 'import json, sys; assert isinstance(json.load(open(sys.argv[1], encoding="utf-8")), dict)' "${VECTOR_DATA_DIR}/manifest.json" >/dev/null 2>&1 && return 0
    return 2
  fi
  [ -e "${VECTOR_DATA_DIR}/active-generation.json" ] || return 1
  if python3 - "$VECTOR_DATA_DIR" <<'PY' >/dev/null 2>&1
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
payload = json.loads((root / "active-generation.json").read_text(encoding="utf-8"))
generation = payload["generation_id"]
model = payload["model_version"]
assert payload["schema_version"] == 1
assert isinstance(generation, str) and generation and Path(generation).name == generation
assert payload["data_dir"] == "generations/" + generation
assert payload["storage_model_version"] == model + "::generation::" + generation
target = (root / "generations" / generation).resolve()
target.relative_to(root)
assert target.parent == (root / "generations").resolve()
assert (target / "manifest.json").is_file()
PY
  then
    return 0
  fi
  return 2
}

ensure_vector_runtime_health() {
  local index_state
  if vector_index_state; then
    index_state=0
  else
    index_state="$?"
  fi
  if [ "$index_state" = "1" ]; then return 0; fi
  if [ "$index_state" != "0" ]; then
    echo "[runtime] invalid active vector index; refusing to start app." >&2
    return 1
  fi
  if [ ! -x "$VECTOR_RUNTIME" ]; then
    echo "[runtime] active vector index requires ${VECTOR_RUNTIME}" >&2
    return 1
  fi
  echo "[runtime] Ensuring vector runtime is healthy on 127.0.0.1:${VECTOR_PORT}..."
  VECTOR_PORT="$VECTOR_PORT" "$VECTOR_RUNTIME" start
  curl -fsS --max-time 2 "http://127.0.0.1:${VECTOR_PORT}/health" >/dev/null
}

verify_vector_runtime_health() {
  local index_state
  if vector_index_state; then
    index_state=0
  else
    index_state="$?"
  fi
  if [ "$index_state" = "1" ]; then return 0; fi
  if [ "$index_state" != "0" ]; then
    echo "[runtime] invalid active vector index; app status is unhealthy." >&2
    return 1
  fi
  if [ ! -x "$VECTOR_RUNTIME" ]; then
    echo "[runtime] active vector index requires ${VECTOR_RUNTIME}" >&2
    return 1
  fi
  VECTOR_PORT="$VECTOR_PORT" "$VECTOR_RUNTIME" status
  curl -fsS --max-time 2 "http://127.0.0.1:${VECTOR_PORT}/health" >/dev/null
}

ensure_venv() {
  if [ ! -x "${VENV_DIR}/bin/python" ]; then
    echo "[runtime] Creating venv..."
    python3 -m venv "$VENV_DIR"
  fi

  if ! "${VENV_DIR}/bin/python" -m pip --version >/dev/null 2>&1; then
    echo "[runtime] Bootstrapping pip in venv..."
    "${VENV_DIR}/bin/python" -m ensurepip --upgrade
  fi

  echo "[runtime] Installing Python dependencies..."
  "${VENV_DIR}/bin/python" -m pip install -r "${APP_DIR}/requirements.txt"
}

stop_app() {
  local stopped_pid=""
  if [ -f "$PID_FILE" ]; then
    old_pid="$(cat "$PID_FILE")"
    if kill -0 "$old_pid" >/dev/null 2>&1; then
      echo "[runtime] Stopping old process ${old_pid}..."
      kill "$old_pid"
      for _ in 1 2 3 4 5 6 7 8 9 10; do
        if ! kill -0 "$old_pid" >/dev/null 2>&1; then
          break
        fi
        sleep 1
      done
      if kill -0 "$old_pid" >/dev/null 2>&1; then
        echo "[runtime] Old process still alive; sending SIGKILL..."
        kill -9 "$old_pid"
      fi
      stopped_pid="$old_pid"
    fi
    rm -f "$PID_FILE"
  fi

  port_pid="$(ss -ltnp "sport = :${APP_PORT}" 2>/dev/null | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | head -n 1 || true)"
  if [ -n "$port_pid" ] && [ "$port_pid" != "$stopped_pid" ] && kill -0 "$port_pid" >/dev/null 2>&1; then
    echo "[runtime] Stopping process ${port_pid} on port ${APP_PORT}..."
    kill "$port_pid"
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      if ! kill -0 "$port_pid" >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
    if kill -0 "$port_pid" >/dev/null 2>&1; then
      echo "[runtime] Port process still alive; sending SIGKILL..."
      kill -9 "$port_pid"
    fi
  fi
}

start_app() {
  ensure_venv
  ensure_vector_runtime_health
  cd "$APP_DIR"
  export PORT="$APP_PORT"
  export DB_MODE="${DB_MODE:-sqlite}"
  export PYTHONUNBUFFERED=1

  echo "[runtime] Initializing database schema..."
  "${VENV_DIR}/bin/python" -c "from feedback_hub import db; c = db.connect(); db.init_schema(c); c.close()"

  echo "[runtime] Starting Feedback Hub on 0.0.0.0:${APP_PORT}..."
  nohup "${VENV_DIR}/bin/python" -m uvicorn feedback_hub.api:app \
    --host 0.0.0.0 \
    --port "$APP_PORT" \
    > "${LOG_DIR}/app.log" 2>&1 &
  echo "$!" > "$PID_FILE"
  echo "[runtime] PID $(cat "$PID_FILE")"
}

status_app() {
  if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" >/dev/null 2>&1; then
    if ! verify_vector_runtime_health; then
      echo "[runtime] app process is running but vector dependency is unhealthy." >&2
      return 1
    fi
    echo "[runtime] running pid=$(cat "$PID_FILE") port=${APP_PORT}"
  else
    echo "[runtime] stopped"
    return 1
  fi
}

if [ "${DEVCLOUD_RUNTIME_LIBRARY:-0}" != "1" ]; then
  case "${1:-restart}" in
    start)
      start_app
      ;;
    stop)
      stop_app
      ;;
    restart)
      stop_app
      start_app
      ;;
    status)
      status_app
      ;;
    logs)
      tail -n "${LINES:-120}" "${LOG_DIR}/app.log"
      ;;
    *)
      echo "Usage: $0 {start|stop|restart|status|logs}" >&2
      exit 2
      ;;
  esac
fi
