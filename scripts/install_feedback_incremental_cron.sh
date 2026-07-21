#!/usr/bin/env bash
# Install the local crontab entry for the 20-minute feedback pipeline.
#
# A stale global installer lock is deliberately not recovered automatically:
# remove it manually only after confirming its owner PID is no longer running.
set -euo pipefail

MARKER='# feedback-hub: managed incremental sync'
PRODUCTION_CRON_ENTRY='*/20 * * * * cd /opt/feedback_hub && PYTHON_BIN=./.venv/bin/python scripts/feedback_incremental_sync.sh'

usage() {
  cat <<'EOF'
Usage: install_feedback_incremental_cron.sh [--project-dir DIR] [--dry-run]

Installs one local crontab entry for the incremental feedback sync. It replaces
only this script's marked/historical generated entry and the exact legacy
two-hour /opt/feedback_hub no-sync job. CRONTAB_BIN may inject a
crontab-compatible binary for tests. This script never connects to a remote.

--dry-run prints the candidate and performs no backup or crontab mutation.
A stale global installer lock is not removed automatically; inspect its owner
and remove it manually only when its process is definitely gone.
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="$SCRIPT_DIR/.."
DRY_RUN=0
CRONTAB_BIN="${CRONTAB_BIN:-crontab}"
TEMP_ROOT="${TMPDIR:-/tmp}"
LOCK_RETRY_ATTEMPTS="${LOCK_RETRY_ATTEMPTS:-300}"
LOCK_RETRY_DELAY="${LOCK_RETRY_DELAY:-0.1}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-dir)
      [[ $# -ge 2 ]] || { echo "--project-dir requires a directory" >&2; exit 2; }
      PROJECT_DIR="$2"
      shift 2
      ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if ! PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd -P)"; then
  echo "Project directory is not accessible: $PROJECT_DIR" >&2
  exit 2
fi
if [[ "$PROJECT_DIR" == *"'"* ]]; then
  echo "Project directory may not contain a single quote for cron shell safety" >&2
  exit 2
fi
if [[ "$PROJECT_DIR" == *%* ]]; then
  echo "Project directory may not contain a percent for cron shell safety" >&2
  exit 2
fi
if [[ "$PROJECT_DIR" =~ [[:cntrl:]] ]]; then
  echo "Project directory may not contain control characters for cron shell safety" >&2
  exit 2
fi
if ! [[ "$LOCK_RETRY_ATTEMPTS" =~ ^[0-9]+$ ]] || (( 10#$LOCK_RETRY_ATTEMPTS < 1 || 10#$LOCK_RETRY_ATTEMPTS > 300 )); then
  echo "LOCK_RETRY_ATTEMPTS must be an integer between 1 and 300" >&2
  exit 2
fi
if ! [[ "$LOCK_RETRY_DELAY" =~ ^(0|[0-9]+(\.[0-9]+)?)$ ]]; then
  echo "LOCK_RETRY_DELAY must be a non-negative decimal" >&2
  exit 2
fi
if ! command -v "$CRONTAB_BIN" >/dev/null 2>&1; then
  echo "crontab binary is not available: $CRONTAB_BIN" >&2
  exit 2
fi
if [[ "$PROJECT_DIR" == "/opt/feedback_hub" ]]; then
  CRON_ENTRY="$PRODUCTION_CRON_ENTRY"
else
  CRON_ENTRY="*/20 * * * * cd '$PROJECT_DIR' && PYTHON_BIN=./.venv/bin/python scripts/feedback_incremental_sync.sh"
fi

umask 077
PRIOR_FILE="$(mktemp "$TEMP_ROOT/feedback-incremental-cron.prior.XXXXXX")"
CANDIDATE_FILE="$(mktemp "$TEMP_ROOT/feedback-incremental-cron.candidate.XXXXXX")"
READBACK_FILE="$(mktemp "$TEMP_ROOT/feedback-incremental-cron.readback.XXXXXX")"
READ_ERROR_FILE="$(mktemp "$TEMP_ROOT/feedback-incremental-cron.error.XXXXXX")"
TOKEN_FILE="$(mktemp "$TEMP_ROOT/feedback-incremental-cron.token.XXXXXX")"
LOCK_TOKEN="$(basename "$TOKEN_FILE")-$$"
rm -f -- "$TOKEN_FILE"
LOCK_DIR="$TEMP_ROOT/feedback-incremental-crontab-${UID}.lock"
LOCK_OWNED=0
had_crontab=0

release_lock() {
  [[ "$LOCK_OWNED" -eq 1 ]] || return 0
  local owner=""
  owner="$(cat "$LOCK_DIR/owner" 2>/dev/null || true)"
  if [[ "$owner" == "$LOCK_TOKEN" ]]; then
    rm -f -- "$LOCK_DIR/owner"
    rmdir -- "$LOCK_DIR" 2>/dev/null || true
  fi
  LOCK_OWNED=0
}

cleanup() {
  release_lock
  rm -f -- "$PRIOR_FILE" "$CANDIDATE_FILE" "$READBACK_FILE" "$READ_ERROR_FILE"
}

on_signal() {
  cleanup
  trap - EXIT
  exit 128
}

trap cleanup EXIT
trap on_signal HUP INT TERM

acquire_lock() {
  local attempt
  for ((attempt = 1; attempt <= 10#$LOCK_RETRY_ATTEMPTS; attempt++)); do
    if mkdir -m 700 "$LOCK_DIR" 2>/dev/null; then
      LOCK_OWNED=1
      if ! printf '%s\n' "$LOCK_TOKEN" > "$LOCK_DIR/owner"; then
        rmdir -- "$LOCK_DIR" 2>/dev/null || true
        LOCK_OWNED=0
        echo "Unable to initialize installer lock" >&2
        return 1
      fi
      return 0
    fi
    sleep "$LOCK_RETRY_DELAY"
  done
  echo "Another feedback crontab installer holds $LOCK_DIR; refusing to remove it automatically" >&2
  return 75
}

read_current_crontab() {
  if "$CRONTAB_BIN" -l > "$PRIOR_FILE" 2> "$READ_ERROR_FILE"; then
    had_crontab=1
    return 0
  fi
  local status=$?
  if [[ "$status" -ne 1 ]]; then
    cat "$READ_ERROR_FILE" >&2
    echo "Unable to read current crontab" >&2
    return "$status"
  fi
  had_crontab=0
  : > "$PRIOR_FILE"
}

render_candidate() {
  awk -v marker="$MARKER" '
    $0 == marker { next }
    /^\*\/20 \* \* \* \* cd ([\047][^\047]+[\047]|\/[^[:space:]]+) && PYTHON_BIN=\.\/\.venv\/bin\/python scripts\/feedback_incremental_sync\.sh$/ { next }
    /^0 \*\/2 \* \* \* cd \/opt\/feedback_hub && scripts\/feedback_daily_sync\.sh --last 150m --no-sync$/ { next }
    { print }
  ' "$PRIOR_FILE" > "$CANDIDATE_FILE"
  printf '%s\n%s\n' "$MARKER" "$CRON_ENTRY" >> "$CANDIDATE_FILE"
}

if [[ "$DRY_RUN" -eq 1 ]]; then
  read_current_crontab
  render_candidate
  printf '%s\n' 'Candidate crontab (dry-run; no backup or crontab mutation):'
  cat "$CANDIDATE_FILE"
  exit 0
fi

acquire_lock
read_current_crontab
render_candidate

BACKUP_DIR="$PROJECT_DIR/feedback_hub/data/cron-backups"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
timestamp="$(date +%Y%m%d-%H%M%S)"
backup_number=0
while :; do
  suffix=""
  (( backup_number == 0 )) || suffix="-$backup_number"
  BACKUP_FILE="$BACKUP_DIR/crontab-$timestamp$suffix.txt"
  if (set -C; : > "$BACKUP_FILE") 2>/dev/null; then break; fi
  ((backup_number += 1))
done
cp "$PRIOR_FILE" "$BACKUP_FILE"
chmod 600 "$BACKUP_FILE"

rollback() {
  echo "Restoring the previous crontab from $BACKUP_FILE" >&2
  if [[ "$had_crontab" -eq 1 ]]; then
    "$CRONTAB_BIN" "$BACKUP_FILE" || echo "Rollback failed" >&2
  else
    "$CRONTAB_BIN" -r || echo "Rollback failed" >&2
  fi
}

if ! "$CRONTAB_BIN" "$CANDIDATE_FILE"; then
  echo "crontab installation failed; rolling back" >&2
  rollback
  exit 1
fi
if ! "$CRONTAB_BIN" -l > "$READBACK_FILE" 2> "$READ_ERROR_FILE"; then
  cat "$READ_ERROR_FILE" >&2
  echo "crontab verification failed; rolling back" >&2
  rollback
  exit 1
fi
entry_count="$(grep -Fxc "$CRON_ENTRY" "$READBACK_FILE" || true)"
marker_count="$(grep -Fxc "$MARKER" "$READBACK_FILE" || true)"
if [[ "$entry_count" != 1 || "$marker_count" != 1 ]] || ! cmp -s "$CANDIDATE_FILE" "$READBACK_FILE"; then
  echo "crontab verification failed: expected one exact managed incremental entry" >&2
  rollback
  exit 1
fi
printf 'Installed incremental feedback cron; backup: %s\n' "$BACKUP_FILE"
