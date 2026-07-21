#!/usr/bin/env bash
# Thin cron wrapper. The Python pipeline owns the only writer lock.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-${PROJECT_DIR}/.venv/bin/python}"
LOG_DIR="${LOG_DIR:-${PROJECT_DIR}/feedback_hub/data/logs}"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"
"$PYTHON_BIN" -m feedback_hub.cli pipeline incremental --pull-window 30m \
  >> "${LOG_DIR}/feedback_incremental_sync.log" 2>&1
