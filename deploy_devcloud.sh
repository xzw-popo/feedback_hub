#!/usr/bin/env bash
# Deploy Feedback Hub code to the CPU DevCloud container without replacing data.
set -eEuo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

SSH_TARGET="${SSH_TARGET:-root@charvelxia-any2.devcloud.woa.com}"
SSH_PORT="${SSH_PORT:-36000}"
REMOTE_DIR="${REMOTE_DIR:-/opt/feedback_hub}"
APP_PORT="${APP_PORT:-8000}"
ARCHIVE="/tmp/feedback_hub_devcloud_$(date +%Y%m%d_%H%M%S).tar.gz"
REMOTE_ARCHIVE="/tmp/feedback_hub_devcloud.tar.gz"
trap 'rm -f "$ARCHIVE"' EXIT

fail() {
  echo "[deploy] $*" >&2
  exit 1
}

validate_remote_setting() {
  case "$SSH_PORT" in ''|*[!0-9]*) fail "SSH_PORT must contain only digits" ;; esac
  case "$SSH_TARGET" in ''|*[!A-Za-z0-9._@-]*) fail "SSH_TARGET contains unsupported characters" ;; esac
  case "$REMOTE_DIR" in /*) ;; *) fail "REMOTE_DIR must be an absolute safe path" ;; esac
  case "$REMOTE_DIR" in *..*|*[!A-Za-z0-9._/-]*) fail "REMOTE_DIR contains unsupported characters" ;; esac
}

remote_command() {
  local command="bash -s --" argument
  for argument in "$@"; do
    printf -v argument '%q' "$argument"
    command+=" ${argument}"
  done
  printf '%s' "$command"
}

validate_remote_setting

echo "[deploy] Building dashboard..."
VITE_API_BASE_URL= npm --prefix dashboard run build

echo "[deploy] Creating archive..."
export COPYFILE_DISABLE=1
TAR_ENV_EXCLUDES=()
while IFS= read -r -d '' ENV_PATH; do
  TAR_ENV_EXCLUDES+=("--exclude=${ENV_PATH#./}")
done < <(find . \( -name '.env' -o -name '.env.*' \) ! -name '.env.example' -print0)
# Persisted paths include feedback_hub/data/models/Qwen3-Embedding-0.6B and feedback_hub/data/vector_index.
tar \
  "${TAR_ENV_EXCLUDES[@]}" \
  --exclude='.git' \
  --exclude='.pytest_cache' \
  --exclude='.vite' \
  --exclude='.DS_Store' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='codex-skills' \
  --exclude='feedback_hub/data' \
  --exclude='feedback_hub/data/models' \
  --exclude='feedback_hub/data/vector_index' \
  --exclude='legacy_cloudbase' \
  --exclude='dashboard/node_modules' \
  --exclude='dashboard/.vite' \
  -czf "$ARCHIVE" .

echo "[deploy] Uploading archive to ${SSH_TARGET}:${REMOTE_DIR}..."
ssh -p "$SSH_PORT" -- "$SSH_TARGET" "mkdir -p -- $(printf '%q' "$REMOTE_DIR")"
scp -P "$SSH_PORT" -- "$ARCHIVE" "${SSH_TARGET}:${REMOTE_ARCHIVE}"

echo "[deploy] Extracting and restarting services..."
ssh -p "$SSH_PORT" -- "$SSH_TARGET" \
  "$(remote_command "$REMOTE_DIR" "$REMOTE_ARCHIVE" "$APP_PORT")" <<'REMOTE_DEPLOY'
set -eEuo pipefail
OLD="$1"
REMOTE_ARCHIVE="$2"
APP_PORT="$3"
NEW="${OLD}.new"
BAK="${OLD}.bak"
FAILED="${OLD}.failed"
OLD_MOVED_TO_BAK=0
NEW_PROMOTED=0
MOVED_VENV=0
MOVED_ENV=0
MOVED_DATA=0
COMMITTED=0
ROLLBACK_DONE=0

# 0=valid active index, 1=no index configured, 2=unsafe/broken metadata.
vector_index_state() {
  local data_dir="${OLD}/feedback_hub/data/vector_index"
  if [ -e "${data_dir}/manifest.json" ]; then
    [ -f "${data_dir}/manifest.json" ] || return 2
    python3 -c 'import json, sys; assert isinstance(json.load(open(sys.argv[1], encoding="utf-8")), dict)' "${data_dir}/manifest.json" >/dev/null 2>&1 && return 0
    return 2
  fi
  [ -e "${data_dir}/active-generation.json" ] || return 1
  if python3 - "$data_dir" <<'PY' >/dev/null 2>&1
import json
import sys
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
PY
  then
    return 0
  fi
  return 2
}

install_deploy_traps() {
  trap 'rollback "$?"' ERR
  trap 'rollback 129' HUP
  trap 'rollback 130' INT
  trap 'rollback 143' TERM
  trap 'on_exit "$?"' EXIT
}

critical_move() {
  local source="$1" destination="$2" marker="$3"
  trap '' HUP INT TERM
  mv -- "$source" "$destination"
  printf -v "$marker" '%s' 1
  install_deploy_traps
}

move_persisted_item() {
  local item="$1" marker="$2"
  [ -e "${OLD}/${item}" ] || return 0
  mkdir -p -- "$(dirname "${NEW}/${item}")"
  critical_move "${OLD}/${item}" "${NEW}/${item}" "$marker"
}

restore_persisted_items() {
  local target="$1" item marker source
  for item in .venv .env feedback_hub/data; do
    case "$item" in
      .venv) marker="MOVED_VENV" ;;
      .env) marker="MOVED_ENV" ;;
      feedback_hub/data) marker="MOVED_DATA" ;;
    esac
    [ "${!marker}" = "1" ] || continue
    source="${NEW}/${item}"
    # Once NEW has been promoted it is named OLD; this also covers a signal in
    # the narrow interval immediately after the atomic directory rename.
    if [ ! -e "$source" ] && [ -e "${OLD}/${item}" ]; then
      source="${OLD}/${item}"
    fi
    [ -e "$source" ] || return 1
    mkdir -p -- "$(dirname "${target}/${item}")"
    rm -rf -- "${target}/${item}"
    mv -- "$source" "${target}/${item}"
    printf -v "$marker" '%s' 0
  done
}

persisted_items_restored() {
  [ "$MOVED_VENV" = "0" ] && [ "$MOVED_ENV" = "0" ] && [ "$MOVED_DATA" = "0" ]
}

rollback() {
  local status="$1"
  [ "$COMMITTED" = "1" ] && return 0
  [ "$ROLLBACK_DONE" = "1" ] && return 0
  ROLLBACK_DONE=1
  set +e
  trap '' HUP INT TERM
  if [ "$NEW_PROMOTED" = "1" ] || { [ -d "$OLD" ] && [ -d "$BAK" ]; }; then
    if [ -x "${OLD}/scripts/devcloud_runtime.sh" ]; then
      cd "$OLD" && APP_PORT="$APP_PORT" scripts/devcloud_runtime.sh stop || true
    fi
    if [ -x "${OLD}/scripts/vector_runtime.sh" ]; then
      cd "$OLD" && scripts/vector_runtime.sh stop || true
    fi
  fi
  if [ "$OLD_MOVED_TO_BAK" = "1" ]; then
    echo "[deploy] Deployment failed; restoring persisted state and previous code." >&2
    restore_persisted_items "$BAK" || { echo "[deploy] Refusing to remove NEW: persisted state was not restored." >&2; exit "$status"; }
    rm -rf -- "$FAILED"
    [ -d "$OLD" ] && mv -- "$OLD" "$FAILED"
    mv -- "$BAK" "$OLD"
    rm -rf -- "$FAILED"
    echo "[deploy] Rollback restored the previous code; app remains stopped for operator review." >&2
  else
    restore_persisted_items "$OLD" || { echo "[deploy] Refusing to remove NEW: persisted state was not restored." >&2; exit "$status"; }
  fi
  if persisted_items_restored; then
    rm -rf -- "$NEW"
  fi
  rm -f -- "$REMOTE_ARCHIVE"
  exit "$status"
}

on_exit() {
  local status="$1"
  [ "$COMMITTED" = "1" ] && return 0
  [ "$ROLLBACK_DONE" = "1" ] && return 0
  rollback "$status"
}

install_deploy_traps

if [ -x "${OLD}/scripts/devcloud_runtime.sh" ]; then
  cd "$OLD" && APP_PORT="$APP_PORT" scripts/devcloud_runtime.sh stop || true
fi
if vector_index_state; then
  VECTOR_REQUIRED=1
else
  VECTOR_INDEX_STATE="$?"
  if [ "$VECTOR_INDEX_STATE" = "1" ]; then
    VECTOR_REQUIRED=0
  else
    echo "[deploy] invalid active vector index; refusing to stop or swap code." >&2
    exit 1
  fi
fi
if [ "$VECTOR_REQUIRED" = "1" ] && [ -x "${OLD}/scripts/vector_runtime.sh" ]; then
  cd "$OLD" && scripts/vector_runtime.sh stop
fi

rm -rf -- "$NEW"
mkdir -p -- "$NEW"
tar -xzf "$REMOTE_ARCHIVE" -C "$NEW"
move_persisted_item .venv MOVED_VENV
move_persisted_item .env MOVED_ENV
move_persisted_item feedback_hub/data MOVED_DATA
rm -rf -- "$BAK"
if [ -d "$OLD" ]; then critical_move "$OLD" "$BAK" OLD_MOVED_TO_BAK; fi
critical_move "${NEW}" "${OLD}" NEW_PROMOTED

cd "$OLD"
chmod +x scripts/devcloud_runtime.sh scripts/vector_runtime.sh
if [ "$VECTOR_REQUIRED" = "1" ]; then
  # vector_runtime waits for loopback /health; app must stay stopped on failure.
  VECTOR_PORT="${VECTOR_PORT:-8011}" scripts/vector_runtime.sh restart
fi
APP_PORT="$APP_PORT" scripts/devcloud_runtime.sh restart

COMMITTED=1
rm -rf -- "$BAK"
rm -f -- "$REMOTE_ARCHIVE"
trap - ERR HUP INT TERM
REMOTE_DEPLOY

echo "[deploy] Done."
echo "[deploy] URL: http://charvelxia-any2.devcloud.woa.com:${APP_PORT}/"
