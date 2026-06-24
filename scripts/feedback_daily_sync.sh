#!/usr/bin/env bash
# Daily user-feedback sync:
#   pull target date -> optional previous-day safety pull -> tag -> sync SQLite to DevCloud -> health check.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

PYTHON_BIN="${PYTHON_BIN:-python3}"
APP_PORT="${APP_PORT:-8000}"
SSH_PORT="${SSH_PORT:-36000}"
SSH_TARGET="${SSH_TARGET:-root@charvelxia-any2.devcloud.woa.com}"
REMOTE_DIR="${REMOTE_DIR:-/opt/feedback_hub}"
DB_PATH="${DB_PATH:-${PROJECT_DIR}/feedback_hub/data/feedback.db}"
LOG_DIR="${LOG_DIR:-${PROJECT_DIR}/feedback_hub/data/logs}"
LOCK_DIR="${LOCK_DIR:-${PROJECT_DIR}/feedback_hub/data/.feedback_daily_sync.lock}"
VPN_CHECK_URL="${VPN_CHECK_URL:-https://wrfeedback.weread.woa.com}"
VPN_MAX_RETRIES="${VPN_MAX_RETRIES:-6}"
VPN_RETRY_SECONDS="${VPN_RETRY_SECONDS:-300}"

DATE=""
DRY_RUN=false
NO_SYNC=false
NO_BACKFILL=false
SKIP_VPN_CHECK=false

usage() {
  cat <<'EOF'
Usage: scripts/feedback_daily_sync.sh [options]

Options:
  --date YYYY-MM-DD       Pull this date instead of yesterday.
  --dry-run               Print actions without running pull/tag/sync.
  --no-sync               Pull and tag locally, but do not sync DevCloud.
  --no-backfill           Do not safety-pull the previous day.
  --skip-vpn-check        Skip OpenAPI reachability check.
  -h, --help              Show this help.

Environment:
  PYTHON_BIN, SSH_TARGET, SSH_PORT, REMOTE_DIR, APP_PORT, VPN_MAX_RETRIES,
  VPN_RETRY_SECONDS, LOG_DIR, DB_PATH
EOF
}

log() {
  local ts
  ts="$(date '+%Y-%m-%d %H:%M:%S')"
  mkdir -p "$LOG_DIR"
  echo "[$ts] $*"
  echo "[$ts] $*" >> "${LOG_DIR}/feedback_daily_sync.log"
}

die() {
  log "ERROR: $*"
  exit 1
}

run() {
  log "+ $*"
  if [[ "$DRY_RUN" == "true" ]]; then
    return 0
  fi
  "$@"
}

date_offset() {
  local base="$1"
  local offset="$2"
  "$PYTHON_BIN" - "$base" "$offset" <<'PY'
import sys
from datetime import datetime, timedelta

base = datetime.strptime(sys.argv[1], "%Y-%m-%d")
offset = int(sys.argv[2])
print((base + timedelta(days=offset)).strftime("%Y-%m-%d"))
PY
}

default_target_date() {
  "$PYTHON_BIN" - <<'PY'
from datetime import datetime, timedelta
print((datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"))
PY
}

acquire_lock() {
  if [[ "$DRY_RUN" == "true" ]]; then
    return 0
  fi
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    die "another feedback_daily_sync run is active: ${LOCK_DIR}"
  fi
  trap 'rm -rf "$LOCK_DIR"' EXIT
}

check_vpn() {
  if [[ "$DRY_RUN" == "true" ]]; then
    log "VPN check skipped (dry-run)"
    return 0
  fi
  if [[ "$SKIP_VPN_CHECK" == "true" ]]; then
    log "VPN check skipped"
    return 0
  fi
  log "Checking OpenAPI reachability: ${VPN_CHECK_URL}"
  local attempt code
  for attempt in $(seq 1 "$VPN_MAX_RETRIES"); do
    code="$(curl -sS -o /dev/null -w "%{http_code}" --max-time 10 -k "$VPN_CHECK_URL" 2>/dev/null || echo "000")"
    if [[ "$code" != "000" && "$code" -lt 500 ]]; then
      log "OpenAPI reachable (HTTP ${code})"
      return 0
    fi
    log "OpenAPI not reachable (HTTP ${code}), attempt ${attempt}/${VPN_MAX_RETRIES}"
    if [[ "$attempt" -lt "$VPN_MAX_RETRIES" ]]; then
      sleep "$VPN_RETRY_SECONDS"
    fi
  done
  die "OpenAPI reachability check failed; VPN/iOA may be disconnected"
}

pull_date() {
  local d="$1"
  log "Pulling feedback for ${d}"
  run "$PYTHON_BIN" -m feedback_hub.cli pull --date "$d"
}

tag_feedback() {
  log "Tagging untagged feedback"
  run "$PYTHON_BIN" -m feedback_hub.cli tag
}

sync_devcloud() {
  if [[ "$NO_SYNC" == "true" ]]; then
    log "DevCloud sync skipped (--no-sync)"
    return 0
  fi

  local remote_tmp="${REMOTE_DIR}/feedback_hub/data/feedback.db.new"
  log "Uploading SQLite database to DevCloud"
  run scp -P "$SSH_PORT" "$DB_PATH" "${SSH_TARGET}:${remote_tmp}"

  log "Replacing remote database and restarting service"
  run ssh -p "$SSH_PORT" "$SSH_TARGET" \
    "cd '${REMOTE_DIR}' && scripts/devcloud_runtime.sh stop && mv feedback_hub/data/feedback.db.new feedback_hub/data/feedback.db && APP_PORT='${APP_PORT}' scripts/devcloud_runtime.sh start"
}

health_check() {
  if [[ "$NO_SYNC" == "true" || "$DRY_RUN" == "true" ]]; then
    log "Health check skipped"
    return 0
  fi
  log "Checking DevCloud API health"
  ssh -p "$SSH_PORT" "$SSH_TARGET" \
    "python3 -c \"import json,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:${APP_PORT}/api/conversations?limit=1', timeout=20); print(r.status, r.headers.get('content-type'))\"" \
    | tee -a "${LOG_DIR}/feedback_daily_sync.log"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --date)
      DATE="${2:-}"
      [[ -n "$DATE" ]] || die "--date requires YYYY-MM-DD"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --no-sync)
      NO_SYNC=true
      shift
      ;;
    --no-backfill)
      NO_BACKFILL=true
      shift
      ;;
    --skip-vpn-check)
      SKIP_VPN_CHECK=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1"
      ;;
  esac
done

main() {
  cd "$PROJECT_DIR"
  mkdir -p "$LOG_DIR"
  acquire_lock

  if [[ -z "$DATE" ]]; then
    DATE="$(default_target_date)"
  fi
  local previous_date
  previous_date="$(date_offset "$DATE" -1)"

  log "========== feedback daily sync start: target=${DATE}, dry_run=${DRY_RUN} =========="
  log "Project: ${PROJECT_DIR}"
  log "Python: ${PYTHON_BIN}"

  check_vpn
  pull_date "$DATE"
  if [[ "$NO_BACKFILL" != "true" ]]; then
    pull_date "$previous_date"
  fi
  tag_feedback
  sync_devcloud
  health_check

  log "========== feedback daily sync done: target=${DATE} =========="
}

main
