#!/usr/bin/env bash
# Manage the loopback-only feedback vector API inside a DevCloud container.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="${APP_DIR}/.venv"
RUN_DIR="${APP_DIR}/run"
LOG_DIR="${APP_DIR}/logs"
PID_FILE="${RUN_DIR}/vector_index.pid"
LOG_FILE="${LOG_DIR}/vector_index.log"
VECTOR_PORT="${VECTOR_PORT:-8011}"
VECTOR_DATA_DIR="${VECTOR_DATA_DIR:-${APP_DIR}/feedback_hub/data/vector_index}"
VECTOR_MODEL_DIR="${VECTOR_MODEL_DIR:-${APP_DIR}/feedback_hub/data/models/Qwen3-Embedding-0.6B}"
VECTOR_DB="${VECTOR_DB:-${APP_DIR}/feedback_hub/data/feedback.db}"

mkdir -p "$RUN_DIR" "$LOG_DIR"

ensure_venv() {
  if [ ! -x "${VENV_DIR}/bin/python" ]; then
    echo "[vector-runtime] Creating venv..."
    python3 -m venv "$VENV_DIR"
  fi
  if ! "${VENV_DIR}/bin/python" -m pip --version >/dev/null 2>&1; then
    "${VENV_DIR}/bin/python" -m ensurepip --upgrade
  fi
  echo "[vector-runtime] Installing service requirements..."
  "${VENV_DIR}/bin/python" -m pip install -r "${APP_DIR}/requirements.txt"
  echo "[vector-runtime] Installing CPU PyTorch..."
  "${VENV_DIR}/bin/python" -m pip install --force-reinstall --index-url https://download.pytorch.org/whl/cpu 'torch>=2.3,<3'
  "${VENV_DIR}/bin/python" -c 'import torch; assert torch.version.cuda is None, "CPU PyTorch is required"'
  echo "[vector-runtime] Installing vector requirements..."
  "${VENV_DIR}/bin/python" -m pip install -r "${APP_DIR}/requirements-vector.txt"
}

pid_is_vector_service() {
  local pid="$1"
  [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1 || return 1
  ps -p "$pid" -o command= 2>/dev/null | grep -F "feedback_hub.cli vectors serve" >/dev/null 2>&1
}

stop_app() {
  if [ ! -f "$PID_FILE" ]; then
    echo "[vector-runtime] stopped"
    return 0
  fi
  local pid
  pid="$(tr -d '[:space:]' < "$PID_FILE")"
  if pid_is_vector_service "$pid"; then
    echo "[vector-runtime] Stopping vector process ${pid}..."
    kill "$pid"
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$pid" >/dev/null 2>&1 || break
      sleep 1
    done
    if kill -0 "$pid" >/dev/null 2>&1; then
      echo "[vector-runtime] Vector process did not stop" >&2
      return 1
    fi
  elif kill -0 "$pid" >/dev/null 2>&1; then
    echo "[vector-runtime] Refusing to signal non-vector PID ${pid}" >&2
    return 1
  fi
  rm -f "$PID_FILE"
  echo "[vector-runtime] stopped"
}

validate_start() {
  if [ "${VECTOR_ALLOW_EMPTY_INDEX:-0}" = "1" ]; then
    return 0
  fi
  if [ ! -d "$VECTOR_MODEL_DIR" ]; then
    echo "[vector-runtime] Missing model directory; set VECTOR_MODEL_DIR or VECTOR_ALLOW_EMPTY_INDEX=1" >&2
    return 1
  fi
  if [ ! -f "${VECTOR_DATA_DIR}/manifest.json" ]; then
    echo "[vector-runtime] Missing active manifest; set VECTOR_ALLOW_EMPTY_INDEX=1 only for bootstrap" >&2
    return 1
  fi
}

wait_for_health() {
  local pid="$1"
  local deadline=$(( $(date +%s) + ${VECTOR_START_TIMEOUT_SECONDS:-60} ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if ! pid_is_vector_service "$pid"; then
      echo "[vector-runtime] Vector process exited before becoming healthy; see ${LOG_FILE}" >&2
      rm -f "$PID_FILE"
      return 1
    fi
    if curl -fsS --max-time 2 "http://127.0.0.1:${VECTOR_PORT}/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "[vector-runtime] Timed out waiting for vector health; see ${LOG_FILE}" >&2
  stop_app || true
  return 1
}

start_app() {
  if [ -f "$PID_FILE" ]; then
    local old_pid
    old_pid="$(tr -d '[:space:]' < "$PID_FILE")"
    if pid_is_vector_service "$old_pid"; then
      echo "[vector-runtime] already running pid=${old_pid} port=${VECTOR_PORT}"
      return 0
    fi
    if kill -0 "$old_pid" >/dev/null 2>&1; then
      echo "[vector-runtime] Refusing stale PID owned by another process" >&2
      return 1
    fi
    rm -f "$PID_FILE"
  fi
  validate_start
  ensure_venv
  cd "$APP_DIR"
  export PYTHONUNBUFFERED=1
  local -a empty_index_args=()
  if [ "${VECTOR_ALLOW_EMPTY_INDEX:-0}" = "1" ]; then
    empty_index_args+=(--allow-empty-index)
  fi
  echo "[vector-runtime] Starting vector API on 127.0.0.1:${VECTOR_PORT}..."
  nohup "${VENV_DIR}/bin/python" -m feedback_hub.cli vectors serve \
    --host 127.0.0.1 --port "$VECTOR_PORT" --db "$VECTOR_DB" \
    --data-dir "$VECTOR_DATA_DIR" --model-dir "$VECTOR_MODEL_DIR" "${empty_index_args[@]}" \
    > "$LOG_FILE" 2>&1 &
  echo "$!" > "$PID_FILE"
  if [ "${VECTOR_ALLOW_EMPTY_INDEX:-0}" != "1" ]; then
    wait_for_health "$(cat "$PID_FILE")"
  fi
  echo "[vector-runtime] PID $(cat "$PID_FILE")"
}

status_app() {
  if [ -f "$PID_FILE" ] && pid_is_vector_service "$(tr -d '[:space:]' < "$PID_FILE")"; then
    echo "[vector-runtime] running pid=$(tr -d '[:space:]' < "$PID_FILE") port=${VECTOR_PORT}"
  else
    if [ -f "$PID_FILE" ] && ! kill -0 "$(tr -d '[:space:]' < "$PID_FILE")" >/dev/null 2>&1; then
      rm -f "$PID_FILE"
    fi
    echo "[vector-runtime] stopped"
    return 1
  fi
}

case "${1:-restart}" in
  start) start_app ;;
  stop) stop_app ;;
  restart) stop_app; start_app ;;
  status) status_app ;;
  logs) tail -n "${LINES:-120}" "$LOG_FILE" ;;
  *) echo "Usage: $0 {start|stop|restart|status|logs}" >&2; exit 2 ;;
esac
