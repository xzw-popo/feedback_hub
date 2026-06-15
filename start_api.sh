#!/bin/bash
export LLM_API_URL="${LLM_API_URL:-https://api.deepseek.com/v1/chat/completions}"
export LLM_MODEL="${LLM_MODEL:-deepseek-v4-flash}"
cd "$(dirname "$0")"
exec /usr/bin/python3 -m uvicorn feedback_hub.api:create_app --factory --host 0.0.0.0 --port 8000
