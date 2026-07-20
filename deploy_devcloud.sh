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
  --exclude='codex-skills' \
  --exclude='legacy_cloudbase' \
  --exclude='dashboard/node_modules' \
  --exclude='dashboard/.vite' \
  -czf "$ARCHIVE" .

echo "[deploy] Uploading archive to ${SSH_TARGET}:${REMOTE_DIR}..."
ssh -p "$SSH_PORT" "$SSH_TARGET" "mkdir -p '$REMOTE_DIR'"
scp -P "$SSH_PORT" "$ARCHIVE" "${SSH_TARGET}:${REMOTE_ARCHIVE}"

echo "[deploy] Extracting and restarting service..."
ssh -p "$SSH_PORT" "$SSH_TARGET" \
  "set -e; OLD='${REMOTE_DIR}'; NEW='${REMOTE_DIR}.new'; BAK='${REMOTE_DIR}.bak'; if [ -x \"\${OLD}/scripts/devcloud_runtime.sh\" ]; then cd \"\${OLD}\" && APP_PORT='${APP_PORT}' scripts/devcloud_runtime.sh stop || true; fi; rm -rf \"\${NEW}\"; mkdir -p \"\${NEW}\"; tar -xzf '${REMOTE_ARCHIVE}' -C \"\${NEW}\"; if [ -d \"\${OLD}/.venv\" ]; then mv \"\${OLD}/.venv\" \"\${NEW}/.venv\"; fi; if [ -f \"\${OLD}/.env\" ]; then mv \"\${OLD}/.env\" \"\${NEW}/.env\"; fi; if [ -d \"\${OLD}/feedback_hub/data\" ]; then mkdir -p \"\${NEW}/feedback_hub\"; rm -rf \"\${NEW}/feedback_hub/data\"; mv \"\${OLD}/feedback_hub/data\" \"\${NEW}/feedback_hub/data\"; fi; rm -rf \"\${BAK}\"; if [ -d \"\${OLD}\" ]; then mv \"\${OLD}\" \"\${BAK}\"; fi; mv \"\${NEW}\" \"\${OLD}\"; cd \"\${OLD}\"; chmod +x scripts/devcloud_runtime.sh; APP_PORT='${APP_PORT}' scripts/devcloud_runtime.sh restart"

echo "[deploy] Done."
echo "[deploy] URL: http://charvelxia-any2.devcloud.woa.com:${APP_PORT}/"
