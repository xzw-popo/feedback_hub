#!/usr/bin/env bash
# Manage the Feedback Hub service inside the DevCloud container.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="${APP_DIR}/.venv"
RUN_DIR="${APP_DIR}/run"
LOG_DIR="${APP_DIR}/logs"
PID_FILE="${RUN_DIR}/feedback_hub.pid"
APP_PORT="${APP_PORT:-8000}"

mkdir -p "$RUN_DIR" "$LOG_DIR" "${APP_DIR}/feedback_hub/data"

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
  cd "$APP_DIR"
  export PORT="$APP_PORT"
  export DB_MODE="${DB_MODE:-sqlite}"
  export PYTHONUNBUFFERED=1

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
    echo "[runtime] running pid=$(cat "$PID_FILE") port=${APP_PORT}"
  else
    echo "[runtime] stopped"
    return 1
  fi
}

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
