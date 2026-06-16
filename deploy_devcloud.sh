#!/usr/bin/env bash
# Deploy Feedback Hub to the CPU DevCloud container.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

SSH_TARGET="${SSH_TARGET:-root@charvelxia-any2.devcloud.woa.com}"
SSH_PORT="${SSH_PORT:-36000}"
REMOTE_DIR="${REMOTE_DIR:-/opt/feedback_hub}"
APP_PORT="${APP_PORT:-8000}"
ARCHIVE="/tmp/feedback_hub_devcloud_$(date +%Y%m%d_%H%M%S).tar.gz"
REMOTE_ARCHIVE="/tmp/feedback_hub_devcloud.tar.gz"

echo "[deploy] Building dashboard..."
VITE_API_BASE_URL= npm --prefix dashboard run build

echo "[deploy] Creating archive..."
export COPYFILE_DISABLE=1
tar \
  --exclude='.git' \
  --exclude='.pytest_cache' \
  --exclude='.vite' \
  --exclude='.DS_Store' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='legacy_cloudbase' \
  --exclude='dashboard/node_modules' \
  --exclude='dashboard/.vite' \
  -czf "$ARCHIVE" .

echo "[deploy] Uploading archive to ${SSH_TARGET}:${REMOTE_DIR}..."
ssh -p "$SSH_PORT" "$SSH_TARGET" "mkdir -p '$REMOTE_DIR'"
scp -P "$SSH_PORT" "$ARCHIVE" "${SSH_TARGET}:${REMOTE_ARCHIVE}"

echo "[deploy] Extracting and restarting service..."
ssh -p "$SSH_PORT" "$SSH_TARGET" \
  "set -e; if [ -x '${REMOTE_DIR}/scripts/devcloud_runtime.sh' ]; then cd '${REMOTE_DIR}' && APP_PORT='${APP_PORT}' scripts/devcloud_runtime.sh stop || true; fi; rm -rf '${REMOTE_DIR}.new'; mkdir -p '${REMOTE_DIR}.new'; tar -xzf '${REMOTE_ARCHIVE}' -C '${REMOTE_DIR}.new'; if [ -d '${REMOTE_DIR}/.venv' ]; then mv '${REMOTE_DIR}/.venv' '${REMOTE_DIR}.new/.venv'; fi; rm -rf '${REMOTE_DIR}.bak'; if [ -d '${REMOTE_DIR}' ]; then mv '${REMOTE_DIR}' '${REMOTE_DIR}.bak'; fi; mv '${REMOTE_DIR}.new' '${REMOTE_DIR}'; cd '${REMOTE_DIR}'; chmod +x scripts/devcloud_runtime.sh; APP_PORT='${APP_PORT}' scripts/devcloud_runtime.sh restart"

echo "[deploy] Done."
echo "[deploy] URL: http://charvelxia-any2.devcloud.woa.com:${APP_PORT}/"
