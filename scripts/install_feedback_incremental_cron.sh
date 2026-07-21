#!/usr/bin/env bash
# Install the local, root crontab entry for the 20-minute feedback pipeline.
#
# --dry-run prints the candidate crontab and deliberately creates neither a
# backup nor a crontab entry.  Normal installation saves the prior crontab in
# feedback_hub/data/cron-backups before asking crontab(1) to install anything.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: install_feedback_incremental_cron.sh [--project-dir DIR] [--dry-run]

Installs one local crontab entry that runs the incremental feedback sync every
20 minutes. Existing incremental entries and the legacy 150-minute no-sync
daily entry are replaced; comments, environment settings, blank lines, and
unrelated jobs are preserved. CRONTAB_BIN may name a crontab-compatible binary
for testing. This script never connects to or executes anything remotely.

--dry-run prints the candidate crontab and performs no backup or crontab write.
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="$SCRIPT_DIR/.."
DRY_RUN=0
CRONTAB_BIN="${CRONTAB_BIN:-crontab}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-dir)
      [[ $# -ge 2 ]] || { echo "--project-dir requires a directory" >&2; exit 2; }
      PROJECT_DIR="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
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
if [[ "$PROJECT_DIR" =~ [[:cntrl:]] ]]; then
  echo "Project directory may not contain control characters for cron shell safety" >&2
  exit 2
fi
if ! command -v "$CRONTAB_BIN" >/dev/null 2>&1; then
  echo "crontab binary is not available: $CRONTAB_BIN" >&2
  exit 2
fi

CRON_ENTRY="*/20 * * * * cd '$PROJECT_DIR' && PYTHON_BIN=./.venv/bin/python scripts/feedback_incremental_sync.sh"
umask 077
TEMP_ROOT="${TMPDIR:-/tmp}"
PRIOR_FILE="$(mktemp "$TEMP_ROOT/feedback-incremental-cron.prior.XXXXXX")"
CANDIDATE_FILE="$(mktemp "$TEMP_ROOT/feedback-incremental-cron.candidate.XXXXXX")"
READBACK_FILE="$(mktemp "$TEMP_ROOT/feedback-incremental-cron.readback.XXXXXX")"
READ_ERROR_FILE="$(mktemp "$TEMP_ROOT/feedback-incremental-cron.error.XXXXXX")"
LOCK_DIR=""
cleanup() {
  rm -f -- "$PRIOR_FILE" "$CANDIDATE_FILE" "$READBACK_FILE" "$READ_ERROR_FILE"
  [[ -z "$LOCK_DIR" ]] || rmdir -- "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT HUP INT TERM

had_crontab=0
if "$CRONTAB_BIN" -l > "$PRIOR_FILE" 2> "$READ_ERROR_FILE"; then
  had_crontab=1
else
  read_status=$?
  if [[ "$read_status" -ne 1 ]]; then
    cat "$READ_ERROR_FILE" >&2
    echo "Unable to read current crontab" >&2
    exit "$read_status"
  fi
  : > "$PRIOR_FILE"
fi

awk '
  /^[[:space:]]*#/ { print; next }
  /^[A-Za-z_][A-Za-z0-9_]*=/ { print; next }
  /feedback_incremental_sync\.sh/ { next }
  /feedback_daily_sync\.sh/ && /--last([[:space:]]+|=)?150m([[:space:]]|$)/ && /--no-sync([[:space:]]|$)/ { next }
  { print }
' "$PRIOR_FILE" > "$CANDIDATE_FILE"
printf '%s\n' "$CRON_ENTRY" >> "$CANDIDATE_FILE"

if [[ "$DRY_RUN" -eq 1 ]]; then
  printf '%s\n' 'Candidate crontab (dry-run; no backup or crontab mutation):'
  cat "$CANDIDATE_FILE"
  exit 0
fi

BACKUP_DIR="$PROJECT_DIR/feedback_hub/data/cron-backups"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
LOCK_DIR="$BACKUP_DIR/.install-feedback-incremental-cron.lock"
for ((attempt = 1; attempt <= 300; attempt++)); do
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    break
  fi
  sleep 0.1
done
if [[ ! -d "$LOCK_DIR" ]]; then
  echo "Timed out waiting for another cron installer" >&2
  exit 75
fi

# Re-read after acquiring the lock so no installer can overwrite a newer job.
if "$CRONTAB_BIN" -l > "$PRIOR_FILE" 2> "$READ_ERROR_FILE"; then
  had_crontab=1
else
  read_status=$?
  if [[ "$read_status" -ne 1 ]]; then
    cat "$READ_ERROR_FILE" >&2
    echo "Unable to read current crontab" >&2
    exit "$read_status"
  fi
  had_crontab=0
  : > "$PRIOR_FILE"
fi
awk '
  /^[[:space:]]*#/ { print; next }
  /^[A-Za-z_][A-Za-z0-9_]*=/ { print; next }
  /feedback_incremental_sync\.sh/ { next }
  /feedback_daily_sync\.sh/ && /--last([[:space:]]+|=)?150m([[:space:]]|$)/ && /--no-sync([[:space:]]|$)/ { next }
  { print }
' "$PRIOR_FILE" > "$CANDIDATE_FILE"
printf '%s\n' "$CRON_ENTRY" >> "$CANDIDATE_FILE"

timestamp="$(date +%Y%m%d-%H%M%S)"
backup_number=0
while :; do
  suffix=""
  (( backup_number == 0 )) || suffix="-$backup_number"
  BACKUP_FILE="$BACKUP_DIR/crontab-$timestamp$suffix.txt"
  if (set -C; : > "$BACKUP_FILE") 2>/dev/null; then
    break
  fi
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
if [[ "$entry_count" != 1 ]] || ! cmp -s "$CANDIDATE_FILE" "$READBACK_FILE"; then
  echo "crontab verification failed: expected one exact incremental entry" >&2
  rollback
  exit 1
fi

printf 'Installed incremental feedback cron; backup: %s\n' "$BACKUP_FILE"
