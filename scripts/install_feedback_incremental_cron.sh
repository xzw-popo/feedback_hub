#!/usr/bin/env bash
# Install the local crontab entry for the 20-minute feedback pipeline.
#
# A stale global installer lock is never removed automatically.
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
A stale lock must be removed manually only after its owner is confirmed dead.
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
TOKEN_FILE="$(mktemp "/tmp/feedback-incremental-crontab-${UID}.token.XXXXXX")"
LOCK_TOKEN="$(basename "$TOKEN_FILE")"
printf '%s %s\n' "$$" "$LOCK_TOKEN" > "$TOKEN_FILE"
LOCK_FILE="/tmp/feedback-incremental-crontab-${UID}.lock"
LOCK_OWNED=0
had_crontab=0
PRIOR_READY=0
INSTALL_MAY_HAVE_MUTATED=0
BACKUP_FILE=""

release_lock() {
  # TOKEN_FILE is fully written before the hard-link acquisition. This also
  # proves ownership if a signal arrives between ln(1) and LOCK_OWNED=1.
  if cmp -s "$TOKEN_FILE" "$LOCK_FILE" 2>/dev/null; then
    rm -f -- "$LOCK_FILE"
  fi
  LOCK_OWNED=0
}

rollback_previous() {
  [[ "$PRIOR_READY" -eq 1 ]] || return 0
  echo "Restoring the previous crontab" >&2
  if [[ "$had_crontab" -eq 1 ]]; then
    [[ -n "$BACKUP_FILE" ]] || return 1
    "$CRONTAB_BIN" "$BACKUP_FILE"
  else
    "$CRONTAB_BIN" -r
  fi
}

verify_previous() {
  [[ "$PRIOR_READY" -eq 1 ]] || return 0
  if [[ "$had_crontab" -eq 1 ]]; then
    "$CRONTAB_BIN" -l > "$READBACK_FILE" 2> "$READ_ERROR_FILE" && cmp -s "$PRIOR_FILE" "$READBACK_FILE"
  else
    ! "$CRONTAB_BIN" -l > "$READBACK_FILE" 2> "$READ_ERROR_FILE"
  fi
}

cleanup() {
  release_lock
  rm -f -- "$PRIOR_FILE" "$CANDIDATE_FILE" "$READBACK_FILE" "$READ_ERROR_FILE" "$TOKEN_FILE"
}

on_signal() {
  trap '' HUP INT TERM
  trap - EXIT
  if [[ "$INSTALL_MAY_HAVE_MUTATED" -eq 1 ]]; then
    rollback_previous || true
    verify_previous || true
  fi
  cleanup
  exit 128
}

trap cleanup EXIT
trap on_signal HUP INT TERM

acquire_lock() {
  local attempt
  for ((attempt = 1; attempt <= 10#$LOCK_RETRY_ATTEMPTS; attempt++)); do
    if ln "$TOKEN_FILE" "$LOCK_FILE" 2>/dev/null; then
      LOCK_OWNED=1
      return 0
    fi
    sleep "$LOCK_RETRY_DELAY"
  done
  echo "Another feedback crontab installer holds $LOCK_FILE; remove only a confirmed stale lock manually" >&2
  return 75
}

read_current_crontab() {
  if "$CRONTAB_BIN" -l > "$PRIOR_FILE" 2> "$READ_ERROR_FILE"; then
    had_crontab=1
    PRIOR_READY=1
    return 0
  else
    local status=$?
  fi
  if [[ "$status" -ne 1 ]]; then
    cat "$READ_ERROR_FILE" >&2
    echo "Unable to read current crontab" >&2
    return "$status"
  fi
  had_crontab=0
  : > "$PRIOR_FILE"
  PRIOR_READY=1
}

render_candidate() {
  awk -v marker="$MARKER" '
    function legacy_job(line, prefix, suffix, body, fields, count, i, name, has_python, has_port) {
      prefix = "0 */2 * * * cd /opt/feedback_hub && "
      suffix = "scripts/feedback_daily_sync.sh --last 150m --no-sync"
      if (index(line, prefix) != 1) return 0
      body = substr(line, length(prefix) + 1)
      if (body ~ / (>>|>) [^[:space:]]+ 2>&1$/) sub(/ (>>|>) [^[:space:]]+ 2>&1$/, "", body)
      if (body == suffix) return 1
      if (substr(body, length(body) - length(suffix) + 1) != suffix) return 0
      body = substr(body, 1, length(body) - length(suffix))
      if (body == "" || body !~ / $/) return 0
      sub(/ $/, "", body)
      count = split(body, fields, " ")
      for (i = 1; i <= count; i++) {
        if (fields[i] !~ /^[A-Za-z_][A-Za-z0-9_]*=[^[:space:]]+$/) return 0
        split(fields[i], pair, "=")
        name = pair[1]
        if (name == "PYTHON_BIN") has_python = 1
        if (name == "APP_PORT") has_port = 1
      }
      return has_python && has_port
    }
    $0 == marker { next }
    /^\*\/20 \* \* \* \* cd ([\047][^\047]+[\047]|\/[^[:space:]]+) && PYTHON_BIN=\.\/\.venv\/bin\/python scripts\/feedback_incremental_sync\.sh$/ { next }
    legacy_job($0) { next }
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

INSTALL_MAY_HAVE_MUTATED=1
if ! "$CRONTAB_BIN" "$CANDIDATE_FILE"; then
  echo "crontab installation failed; rolling back" >&2
  rollback_previous || echo "Rollback failed" >&2
  INSTALL_MAY_HAVE_MUTATED=0
  exit 1
fi
if ! "$CRONTAB_BIN" -l > "$READBACK_FILE" 2> "$READ_ERROR_FILE"; then
  cat "$READ_ERROR_FILE" >&2
  echo "crontab verification failed; rolling back" >&2
  rollback_previous || echo "Rollback failed" >&2
  INSTALL_MAY_HAVE_MUTATED=0
  exit 1
fi
entry_count="$(grep -Fxc "$CRON_ENTRY" "$READBACK_FILE" || true)"
marker_count="$(grep -Fxc "$MARKER" "$READBACK_FILE" || true)"
if [[ "$entry_count" != 1 || "$marker_count" != 1 ]] || ! cmp -s "$CANDIDATE_FILE" "$READBACK_FILE"; then
  echo "crontab verification failed: expected one exact managed incremental entry" >&2
  rollback_previous || echo "Rollback failed" >&2
  INSTALL_MAY_HAVE_MUTATED=0
  exit 1
fi
INSTALL_MAY_HAVE_MUTATED=0
printf 'Installed incremental feedback cron; backup: %s\n' "$BACKUP_FILE"
