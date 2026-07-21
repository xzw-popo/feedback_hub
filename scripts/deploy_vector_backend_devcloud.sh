#!/usr/bin/env bash
# Bootstrap the persisted Qwen embedding model and vector dependencies on DevCloud.
# This script deliberately does not pull feedback, tag rows, rebuild vectors, or edit cron.
set -eEuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH_TARGET="${SSH_TARGET:-root@charvelxia-any2.devcloud.woa.com}"
SSH_PORT="${SSH_PORT:-36000}"
REMOTE_DIR="${REMOTE_DIR:-/opt/feedback_hub}"
REMOTE_MODEL_DIR="${REMOTE_DIR}/feedback_hub/data/models/Qwen3-Embedding-0.6B"
REMOTE_VECTOR_INDEX_DIR="${REMOTE_DIR}/feedback_hub/data/vector_index"
MODEL_SOURCE_DIR="${MODEL_SOURCE_DIR:-${PROJECT_ROOT}/feedback_hub/data/embedding_lab/models/Qwen3-Embedding-0.6B}"
START_AFTER_BOOTSTRAP=0
MANIFEST="$(mktemp "${TMPDIR:-/tmp}/feedback-vector-model-manifest.XXXXXX")"
CHANGED_FILES="$(mktemp "${TMPDIR:-/tmp}/feedback-vector-model-changed.XXXXXX")"
RUN_ID="$(date +%s).$$"
REMOTE_MANIFEST="${REMOTE_DIR}/.vector-model-manifest.${RUN_ID}.sha256.new"
REMOTE_STAGE="${REMOTE_MODEL_DIR}.upload.${RUN_ID}"
REMOTE_PREPARED=0

cleanup() {
  local status="$?"
  if [ "$REMOTE_PREPARED" = "1" ]; then
    ssh -p "$SSH_PORT" -- "$SSH_TARGET" \
      "rm -rf -- $(printf '%q' "$REMOTE_STAGE"); rm -f -- $(printf '%q' "$REMOTE_MANIFEST")" >/dev/null 2>&1 || true
  fi
  rm -f "$MANIFEST" "$CHANGED_FILES"
  return "$status"
}
trap cleanup EXIT

usage() {
  echo "Usage: $0 [--start-after-bootstrap]" >&2
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --start-after-bootstrap) START_AFTER_BOOTSTRAP=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
  shift
done

fail() {
  echo "[vector-deploy] $*" >&2
  exit 1
}

validate_remote_setting() {
  case "$SSH_PORT" in ''|*[!0-9]*) fail "SSH_PORT must contain only digits" ;; esac
  case "$SSH_TARGET" in ''|*[!A-Za-z0-9._@-]*) fail "SSH_TARGET contains unsupported characters" ;; esac
  case "$REMOTE_DIR" in /*) ;; *) fail "REMOTE_DIR must be an absolute safe path" ;; esac
  case "$REMOTE_DIR" in *..*|*[!A-Za-z0-9._/-]*) fail "REMOTE_DIR contains unsupported characters" ;; esac
}

create_manifest() {
  python3 - "$MODEL_SOURCE_DIR" "$MANIFEST" <<'PY'
from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

source_root = Path(sys.argv[1])
if source_root.is_symlink():
    raise SystemExit(f"[vector-deploy] Model source root must not be a symlink: {source_root}")
model_dir = source_root.resolve()
manifest = Path(sys.argv[2])
if not model_dir.is_dir():
    raise SystemExit(f"[vector-deploy] Model directory does not exist: {model_dir}")
required = ("config.json", "tokenizer.json", "model.safetensors")
for name in required:
    candidate = model_dir / name
    if candidate.is_symlink() or not candidate.is_file():
        raise SystemExit(f"[vector-deploy] Missing required model file: {candidate}")

entries: list[tuple[str, str]] = []
safe_path = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z")
for directory, dirnames, filenames in os.walk(model_dir, followlinks=False):
    current = Path(directory)
    for name in [*dirnames, *filenames]:
        if (current / name).is_symlink():
            raise SystemExit(f"[vector-deploy] Model tree must not contain symlink: {current / name}")
    for filename in filenames:
        path = current / filename
        if not path.is_file():
            raise SystemExit(f"[vector-deploy] Model tree entry is not a regular file: {path}")
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(model_dir).as_posix()
        except ValueError as exc:
            raise SystemExit(f"[vector-deploy] Model file escapes source root: {path}") from exc
        if not safe_path.fullmatch(relative) or "//" in relative or "/../" in f"/{relative}/":
            raise SystemExit(f"[vector-deploy] Unsafe model filename: {relative!r}")
        digest = hashlib.sha256()
        with resolved.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        entries.append((relative, digest.hexdigest()))
entries.sort()
lines: list[str] = []
for relative, digest in entries:
    if not safe_path.fullmatch(relative) or "//" in relative or "/../" in f"/{relative}/":
        raise SystemExit(f"[vector-deploy] Unsafe model filename: {relative!r}")
    lines.append(f"{digest}  {relative}\n")
if not entries:
    raise SystemExit("[vector-deploy] Model directory has no files")
manifest.write_text("".join(lines), encoding="utf-8")
PY
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
create_manifest

echo "[vector-deploy] Uploading deterministic checksum manifest..."
ssh -p "$SSH_PORT" -- "$SSH_TARGET" "mkdir -p -- $(printf '%q' "$REMOTE_DIR")"
scp -P "$SSH_PORT" -- "$MANIFEST" "${SSH_TARGET}:${REMOTE_MANIFEST}"

echo "[vector-deploy] Preparing only changed model files..."
ssh -p "$SSH_PORT" -- "$SSH_TARGET" \
  "$(remote_command "$REMOTE_MODEL_DIR" "$REMOTE_MANIFEST" "$REMOTE_STAGE")" >"$CHANGED_FILES" <<'REMOTE_PREPARE'
set -euo pipefail
MODEL_DIR="$1"
MANIFEST="$2"
STAGE="$3"

manifest_is_safe() {
  local digest relative extra
  while IFS=' ' read -r digest relative extra; do
    [ -z "$extra" ] || return 1
    [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || return 1
    [[ "$relative" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] || return 1
    [[ "$relative" != *".."* && "$relative" != */./* && "$relative" != *"//"* ]] || return 1
  done < "$MANIFEST"
}
sha256_file() { sha256sum "$1" | awk '{print $1}'; }

manifest_is_safe || { echo "unsafe checksum manifest" >&2; exit 1; }
cleanup_partial() {
  rm -rf -- "$STAGE"
  rm -f -- "$MANIFEST"
}
trap cleanup_partial ERR INT TERM
rm -rf -- "$STAGE"
mkdir -p -- "$STAGE"
while IFS=' ' read -r digest relative; do
  target="${STAGE}/${relative}"
  mkdir -p -- "$(dirname "$target")"
  if [ -f "${MODEL_DIR}/${relative}" ] && [ "$(sha256_file "${MODEL_DIR}/${relative}")" = "$digest" ]; then
    cp -- "${MODEL_DIR}/${relative}" "$target"
  else
    printf '%s\n' "$relative"
  fi
done < "$MANIFEST"
trap - ERR INT TERM
REMOTE_PREPARE
REMOTE_PREPARED=1

while IFS= read -r relative; do
  [[ "$relative" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] || fail "Remote returned unsafe model filename"
  echo "[vector-deploy] Uploading changed file: ${relative}"
  scp -P "$SSH_PORT" -- "${MODEL_SOURCE_DIR}/${relative}" "${SSH_TARGET}:${REMOTE_STAGE}/${relative}"
done < "$CHANGED_FILES"

echo "[vector-deploy] Verifying model, promoting it atomically, and installing vector runtime..."
ssh -p "$SSH_PORT" -- "$SSH_TARGET" \
  "$(remote_command "$REMOTE_DIR" "$REMOTE_MODEL_DIR" "$REMOTE_VECTOR_INDEX_DIR" "$REMOTE_MANIFEST" "$REMOTE_STAGE" "$START_AFTER_BOOTSTRAP")" <<'REMOTE_FINISH'
set -eEuo pipefail
APP_DIR="$1"
MODEL_DIR="$2"
VECTOR_INDEX_DIR="$3"
MANIFEST="$4"
STAGE="$5"
START_AFTER_BOOTSTRAP="$6"
BACKUP="${MODEL_DIR}.previous"
PROMOTION_LIVE_MOVED=0
PROMOTION_STAGE_LIVE=0

restore_previous_model() {
  set +e
  if [ "$PROMOTION_STAGE_LIVE" = "1" ] && [ -d "$MODEL_DIR" ]; then
    rm -rf -- "${STAGE}.failed"
    mv -- "$MODEL_DIR" "${STAGE}.failed" || return 1
  fi
  if [ "$PROMOTION_LIVE_MOVED" = "1" ] && [ -d "$BACKUP" ]; then
    mv -- "$BACKUP" "$MODEL_DIR" || return 1
  fi
  return 0
}

cleanup_promotion() {
  local status="$1"
  restore_previous_model || echo "[vector-deploy] Model rollback could not restore previous live model." >&2
  rm -rf -- "$STAGE"
  rm -f -- "$MANIFEST"
  exit "$status"
}
trap 'cleanup_promotion "$?"' ERR
trap 'cleanup_promotion 130' INT
trap 'cleanup_promotion 143' TERM

manifest_is_safe() {
  local digest relative extra
  while IFS=' ' read -r digest relative extra; do
    [ -z "$extra" ] || return 1
    [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || return 1
    [[ "$relative" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] || return 1
    [[ "$relative" != *".."* && "$relative" != */./* && "$relative" != *"//"* ]] || return 1
  done < "$MANIFEST"
}
sha256_file() { sha256sum "$1" | awk '{print $1}'; }
has_safe_active_index() {
  if [ -e "${VECTOR_INDEX_DIR}/manifest.json" ]; then
    [ -f "${VECTOR_INDEX_DIR}/manifest.json" ] || return 1
    python3 -c 'import json, sys; assert isinstance(json.load(open(sys.argv[1], encoding="utf-8")), dict)' "${VECTOR_INDEX_DIR}/manifest.json" >/dev/null 2>&1 && return 0
    return 1
  fi
  python3 - "$VECTOR_INDEX_DIR" <<'PY'
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

manifest_is_safe || { echo "unsafe checksum manifest" >&2; exit 1; }
while IFS=' ' read -r digest relative; do
  [ -f "${STAGE}/${relative}" ] || { echo "missing staged model file: ${relative}" >&2; exit 1; }
  [ "$(sha256_file "${STAGE}/${relative}")" = "$digest" ] || { echo "checksum mismatch: ${relative}" >&2; exit 1; }
done < "$MANIFEST"
cp -- "$MANIFEST" "$STAGE/.manifest.sha256"
cmp -s "$MANIFEST" "$STAGE/.manifest.sha256"

if ! cmp -s "$MANIFEST" "${MODEL_DIR}/.manifest.sha256" 2>/dev/null; then
  rm -rf -- "$BACKUP"
  if [ -d "$MODEL_DIR" ]; then mv -- "$MODEL_DIR" "$BACKUP"; PROMOTION_LIVE_MOVED=1; fi
  mv -- "$STAGE" "$MODEL_DIR"
  PROMOTION_STAGE_LIVE=1
else
  rm -rf -- "$STAGE"
fi
rm -f -- "$MANIFEST"

cd "$APP_DIR"
if [ ! -x .venv/bin/python ]; then python3 -m venv .venv; fi
./.venv/bin/python -m pip install -r requirements-vector.txt
./.venv/bin/python -m pip install --force-reinstall --index-url https://download.pytorch.org/whl/cpu 'torch>=2.3,<3'
./.venv/bin/python -c 'import torch; assert torch.version.cuda is None, "CPU PyTorch is required"'

if [ "$START_AFTER_BOOTSTRAP" = "1" ]; then
  if has_safe_active_index; then
    VECTOR_PORT="${VECTOR_PORT:-8011}" scripts/vector_runtime.sh start
  else
    echo "[vector-deploy] No active vector manifest; bootstrap completed without starting the vector API."
  fi
fi
rm -rf -- "$BACKUP"
PROMOTION_LIVE_MOVED=0
PROMOTION_STAGE_LIVE=0
trap - ERR INT TERM
REMOTE_FINISH
REMOTE_PREPARED=0

echo "[vector-deploy] Bootstrap complete. No feedback pull, backfill, tagging, vector rebuild, or cron change was run."
