#!/usr/bin/env bash
# Thin cron wrapper. The Python pipeline owns the only writer lock.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-${PROJECT_DIR}/.venv/bin/python}"
LOG_DIR="${LOG_DIR:-${PROJECT_DIR}/feedback_hub/data/logs}"
LOG_FILE="${LOG_DIR}/feedback_incremental_sync.log"
LOG_MAX_BYTES="${LOG_MAX_BYTES:-10485760}"
LOG_KEEP="${LOG_KEEP:-5}"

parse_decimal_setting() {
  local setting="$1"
  local raw="$2"
  local maximum="$3"
  local normalized="$raw"
  local number

  case "$raw" in
    ''|*[!0-9]*) echo "${setting} must contain decimal digits only" >&2; return 1 ;;
  esac
  # Bound parsing work before stripping leading zeroes and avoid bash octal
  # arithmetic (for example, 0008 must mean decimal eight).
  if (( ${#raw} > 64 )); then
    echo "${setting} is too long" >&2
    return 1
  fi
  while [[ ${#normalized} -gt 1 && ${normalized:0:1} == "0" ]]; do
    normalized="${normalized:1}"
  done
  if (( ${#normalized} > 10 )); then
    echo "${setting} is out of range" >&2
    return 1
  fi
  number=$((10#$normalized))
  if (( number < 1 || number > maximum )); then
    echo "${setting} is out of range" >&2
    return 1
  fi
  printf -v "$setting" '%d' "$number"
}

if ! parse_decimal_setting LOG_MAX_BYTES "$LOG_MAX_BYTES" 1073741824; then
  echo "LOG_MAX_BYTES must be an integer between 1 and 1073741824" >&2
  exit 1
fi
if ! parse_decimal_setting LOG_KEEP "$LOG_KEEP" 20; then
  echo "LOG_KEEP must be an integer between 1 and 20" >&2
  exit 1
fi

rotate_log() {
  [[ -f "$LOG_FILE" ]] || return 0
  local size
  size="$({ wc -c < "$LOG_FILE"; } 2>/dev/null || printf '0')"
  size="${size//[[:space:]]/}"
  [[ "$size" =~ ^[0-9]+$ ]] || return 0
  (( size >= LOG_MAX_BYTES )) || return 0

  if [[ -e "${LOG_FILE}.${LOG_KEEP}" ]]; then
    rm -f -- "${LOG_FILE}.${LOG_KEEP}" 2>/dev/null || true
  fi
  local index
  for ((index = LOG_KEEP - 1; index >= 1; index--)); do
    if [[ -e "${LOG_FILE}.${index}" ]]; then
      mv -- "${LOG_FILE}.${index}" "${LOG_FILE}.$((index + 1))" 2>/dev/null || true
    fi
  done
  if [[ -e "$LOG_FILE" ]]; then
    mv -- "$LOG_FILE" "${LOG_FILE}.1" 2>/dev/null || true
  fi
}

mkdir -p "$LOG_DIR"
rotate_log || true
cd "$PROJECT_DIR"
"$PYTHON_BIN" -m feedback_hub.cli pipeline incremental --pull-window 30m \
  >> "$LOG_FILE" 2>&1
