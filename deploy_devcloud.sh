#!/usr/bin/env bash
# Deploy Feedback Hub code to the CPU DevCloud container without replacing data.
set -euo pipefail

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
set -euo pipefail
OLD="$1"
REMOTE_ARCHIVE="$2"
APP_PORT="$3"
NEW="${OLD}.new"
BAK="${OLD}.bak"
FAILED="${OLD}.failed"
SWAPPED=0

vector_index_is_active() {
  local data_dir="${OLD}/feedback_hub/data/vector_index"
  [ -f "${data_dir}/manifest.json" ] && return 0
  python3 - "$data_dir" <<'PY' >/dev/null 2>&1
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
}

restore_runtime_state() {
  local item
  for item in .venv .env feedback_hub/data; do
    if [ -e "${OLD}/${item}" ]; then
      mkdir -p -- "$(dirname "${BAK}/${item}")"
      rm -rf -- "${BAK}/${item}"
      mv -- "${OLD}/${item}" "${BAK}/${item}"
    fi
  done
}

rollback() {
  local status="$?"
  set +e
  if [ "$SWAPPED" = "1" ] && [ -d "$BAK" ]; then
    echo "[deploy] Deployment failed; rolling code directory back." >&2
    if [ -x "${OLD}/scripts/devcloud_runtime.sh" ]; then
      cd "$OLD" && APP_PORT="$APP_PORT" scripts/devcloud_runtime.sh stop || true
    fi
    if [ -x "${OLD}/scripts/vector_runtime.sh" ]; then
      cd "$OLD" && scripts/vector_runtime.sh stop || true
    fi
    restore_runtime_state
    rm -rf -- "$FAILED"
    mv -- "$OLD" "$FAILED"
    mv -- "$BAK" "$OLD"
    rm -rf -- "$FAILED"
    echo "[deploy] Rollback restored the previous code; app remains stopped for operator review." >&2
  fi
  rm -rf -- "$NEW"
  rm -f -- "$REMOTE_ARCHIVE"
  exit "$status"
}
trap rollback ERR INT TERM

if [ -x "${OLD}/scripts/devcloud_runtime.sh" ]; then
  cd "$OLD" && APP_PORT="$APP_PORT" scripts/devcloud_runtime.sh stop || true
fi
if [ -x "${OLD}/scripts/vector_runtime.sh" ] && vector_index_is_active; then
  cd "$OLD" && scripts/vector_runtime.sh stop
fi

rm -rf -- "$NEW"
mkdir -p -- "$NEW"
tar -xzf "$REMOTE_ARCHIVE" -C "$NEW"
if [ -d "${OLD}/.venv" ]; then mv -- "${OLD}/.venv" "${NEW}/.venv"; fi
if [ -f "${OLD}/.env" ]; then mv -- "${OLD}/.env" "${NEW}/.env"; fi
if [ -d "${OLD}/feedback_hub/data" ]; then
  mkdir -p -- "${NEW}/feedback_hub"
  rm -rf -- "${NEW}/feedback_hub/data"
  mv -- "${OLD}/feedback_hub/data" "${NEW}/feedback_hub/data"
fi
rm -rf -- "$BAK"
if [ -d "$OLD" ]; then mv -- "$OLD" "$BAK"; fi
mv "${NEW}" "${OLD}"
SWAPPED=1

cd "$OLD"
chmod +x scripts/devcloud_runtime.sh scripts/vector_runtime.sh
if vector_index_is_active; then
  # vector_runtime waits for loopback /health; app must stay stopped on failure.
  VECTOR_PORT="${VECTOR_PORT:-8011}" scripts/vector_runtime.sh restart
fi
APP_PORT="$APP_PORT" scripts/devcloud_runtime.sh restart

SWAPPED=0
rm -rf -- "$BAK"
rm -f -- "$REMOTE_ARCHIVE"
trap - ERR INT TERM
REMOTE_DEPLOY

echo "[deploy] Done."
echo "[deploy] URL: http://charvelxia-any2.devcloud.woa.com:${APP_PORT}/"
