#!/usr/bin/env bash
# Thin cron wrapper. The Python pipeline owns the only writer lock.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-${PROJECT_DIR}/.venv/bin/python}"
LOG_DIR="${LOG_DIR:-${PROJECT_DIR}/feedback_hub/data/logs}"
LOG_FILE="${LOG_DIR}/feedback_incremental_sync.log"
LOG_MAX_BYTES="${LOG_MAX_BYTES:-10485760}"
LOG_KEEP="${LOG_KEEP:-5}"

case "$LOG_MAX_BYTES" in
  ''|*[!0-9]*) echo "LOG_MAX_BYTES must be a positive integer" >&2; exit 1 ;;
esac
case "$LOG_KEEP" in
  ''|*[!0-9]*) echo "LOG_KEEP must be a positive integer" >&2; exit 1 ;;
esac
if (( LOG_MAX_BYTES < 1 || LOG_KEEP < 1 )); then
  echo "LOG_MAX_BYTES and LOG_KEEP must be positive" >&2
  exit 1
fi

rotate_log() {
  [[ -f "$LOG_FILE" ]] || return 0
  local size
  size="$(wc -c < "$LOG_FILE" | tr -d '[:space:]')"
  [[ "$size" =~ ^[0-9]+$ ]] || return 0
  (( size >= LOG_MAX_BYTES )) || return 0

  if [[ -f "${LOG_FILE}.${LOG_KEEP}" ]]; then
    rm -f -- "${LOG_FILE}.${LOG_KEEP}"
  fi
  local index
  for ((index = LOG_KEEP - 1; index >= 1; index--)); do
    if [[ -f "${LOG_FILE}.${index}" ]]; then
      mv -- "${LOG_FILE}.${index}" "${LOG_FILE}.$((index + 1))"
    fi
  done
  mv -- "$LOG_FILE" "${LOG_FILE}.1"
}

mkdir -p "$LOG_DIR"
rotate_log
cd "$PROJECT_DIR"
"$PYTHON_BIN" -m feedback_hub.cli pipeline incremental --pull-window 30m \
  >> "$LOG_FILE" 2>&1
