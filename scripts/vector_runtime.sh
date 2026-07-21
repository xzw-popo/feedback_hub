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

RUNTIME_PID=""
RUNTIME_TOKEN=""

read_pid_record() {
  [ -f "$PID_FILE" ] || return 1
  local extra=""
  read -r RUNTIME_PID RUNTIME_TOKEN extra < "$PID_FILE" || return 1
  case "$RUNTIME_PID" in ''|*[!0-9]*) return 1 ;; esac
  case "$RUNTIME_TOKEN" in ''|*[!A-Za-z0-9]*) return 1 ;; esac
  [ -z "$extra" ]
}

pid_is_owned() {
  local pid="$1" token="$2" command=""
  kill -0 "$pid" >/dev/null 2>&1 || return 1
  command="$(ps -p "$pid" -o command= 2>/dev/null)"
  case " $command " in
    *" -m feedback_hub.cli vectors serve "*" --runtime-token ${token} "*) return 0 ;;
    *) return 1 ;;
  esac
}

remove_pid_record_if_owned() {
  local pid="$1" token="$2"
  if read_pid_record && [ "$RUNTIME_PID" = "$pid" ] && [ "$RUNTIME_TOKEN" = "$token" ]; then
    rm -f "$PID_FILE"
  fi
}

stop_app() {
  if ! read_pid_record; then
    if [ -f "$PID_FILE" ]; then
      echo "[vector-runtime] Refusing malformed PID record" >&2
      return 1
    fi
    echo "[vector-runtime] stopped"
    return 0
  fi
  local pid="$RUNTIME_PID" token="$RUNTIME_TOKEN"
  if pid_is_owned "$pid" "$token"; then
    echo "[vector-runtime] Stopping vector process ${pid}..."
    kill -TERM "$pid"
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$pid" >/dev/null 2>&1 || break
      sleep 1
    done
    if kill -0 "$pid" >/dev/null 2>&1; then
      echo "[vector-runtime] Sending SIGKILL to owned vector process ${pid}..." >&2
      kill -9 "$pid"
      for _ in 1 2 3 4 5; do
        kill -0 "$pid" >/dev/null 2>&1 || break
        sleep 1
      done
      if kill -0 "$pid" >/dev/null 2>&1; then
        echo "[vector-runtime] Vector process did not stop" >&2
        return 1
      fi
    fi
  elif kill -0 "$pid" >/dev/null 2>&1; then
    echo "[vector-runtime] Refusing to signal PID ${pid}: ownership token does not match" >&2
    return 1
  fi
  remove_pid_record_if_owned "$pid" "$token"
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
  if [ -f "${VECTOR_DATA_DIR}/manifest.json" ]; then
    return 0
  fi
  if ! python3 -c '
import json, sys
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
' "$VECTOR_DATA_DIR" >/dev/null 2>&1; then
    echo "[vector-runtime] Missing active manifest; set VECTOR_ALLOW_EMPTY_INDEX=1 only for bootstrap" >&2
    return 1
  fi
}

wait_for_health() {
  local pid="$1" token="$2"
  local deadline=$(( $(date +%s) + ${VECTOR_START_TIMEOUT_SECONDS:-60} ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if ! pid_is_owned "$pid" "$token"; then
      echo "[vector-runtime] Vector process exited before becoming healthy; see ${LOG_FILE}" >&2
      remove_pid_record_if_owned "$pid" "$token"
      return 1
    fi
    if curl -fsS --max-time 2 "http://127.0.0.1:${VECTOR_PORT}/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "[vector-runtime] Timed out waiting for vector health; see ${LOG_FILE}" >&2
  stop_app
}

wait_for_process() {
  local pid="$1" token="$2"
  local deadline=$(( $(date +%s) + ${VECTOR_BOOTSTRAP_START_GRACE_SECONDS:-2} ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if ! pid_is_owned "$pid" "$token"; then
      echo "[vector-runtime] Vector process exited during bootstrap; see ${LOG_FILE}" >&2
      remove_pid_record_if_owned "$pid" "$token"
      return 1
    fi
    sleep 1
  done
  if ! pid_is_owned "$pid" "$token"; then
    echo "[vector-runtime] Vector process exited during bootstrap; see ${LOG_FILE}" >&2
    remove_pid_record_if_owned "$pid" "$token"
    return 1
  fi
}

start_app() {
  if [ -f "$PID_FILE" ]; then
    if ! read_pid_record; then
      echo "[vector-runtime] Refusing malformed PID record" >&2
      return 1
    fi
    local old_pid="$RUNTIME_PID" old_token="$RUNTIME_TOKEN"
    if pid_is_owned "$old_pid" "$old_token"; then
      if [ "${VECTOR_ALLOW_EMPTY_INDEX:-0}" != "1" ]; then
        wait_for_health "$old_pid" "$old_token"
      fi
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
  local token
  token="$("${VENV_DIR}/bin/python" -c 'import secrets; print(secrets.token_hex(16))')"
  echo "[vector-runtime] Starting vector API on 127.0.0.1:${VECTOR_PORT}..."
  nohup "${VENV_DIR}/bin/python" -m feedback_hub.cli vectors serve \
    --host 127.0.0.1 --port "$VECTOR_PORT" --db "$VECTOR_DB" \
    --data-dir "$VECTOR_DATA_DIR" --model-dir "$VECTOR_MODEL_DIR" --runtime-token "$token" "${empty_index_args[@]}" \
    > "$LOG_FILE" 2>&1 &
  printf '%s %s\n' "$!" "$token" > "$PID_FILE"
  if [ "${VECTOR_ALLOW_EMPTY_INDEX:-0}" != "1" ]; then
    wait_for_health "$!" "$token"
  else
    wait_for_process "$!" "$token"
  fi
  echo "[vector-runtime] PID $!"
}

status_app() {
  if read_pid_record && pid_is_owned "$RUNTIME_PID" "$RUNTIME_TOKEN"; then
    if [ "${VECTOR_ALLOW_EMPTY_INDEX:-0}" != "1" ] \
       && ! curl -fsS --max-time 2 "http://127.0.0.1:${VECTOR_PORT}/health" >/dev/null 2>&1; then
      echo "[vector-runtime] unhealthy pid=${RUNTIME_PID} port=${VECTOR_PORT}" >&2
      return 1
    fi
    echo "[vector-runtime] running pid=${RUNTIME_PID} port=${VECTOR_PORT}"
  else
    if read_pid_record && ! kill -0 "$RUNTIME_PID" >/dev/null 2>&1; then
      remove_pid_record_if_owned "$RUNTIME_PID" "$RUNTIME_TOKEN"
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
