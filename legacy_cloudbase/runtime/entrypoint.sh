#!/usr/bin/env bash
set -e

echo "[entrypoint] Starting feedback_hub on port ${PORT:-9000}..."
exec python3 -m uvicorn feedback_hub.api:app \
    --host 0.0.0.0 \
    --port "${PORT:-9000}"
