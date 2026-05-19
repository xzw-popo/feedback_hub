"""Wink LLM Agent HTTP 客户端（spec §5.2 第 2 步 LLM 兜底依赖）。

协议：
- POST {AGENT_RUN_URL}  body={"agent_key": ..., "query": ...}
    → {"err_code":0, "session_id":..., "chat_id":...}
- POST {AGENT_POLL_URL} body={"session_id":..., "chat_id":...} 轮询
    → {"err_code":0, "content": ..., "has_more": bool, "interval": ms}

设计：
- 不做流式拼接（已知 has_more=False 时一次性返回完整 content）
- run/poll 任一阶段 err_code != 0 直接抛 RuntimeError
- 单次重试，重试间隔指数后退
- 超时由参数控制；默认 60s
"""
from __future__ import annotations

import time
from typing import Optional

import requests

from feedback_hub.config import AGENT_POLL_URL, AGENT_RUN_URL, get_agent_key


class LLMClientError(RuntimeError):
    """LLM 客户端业务/协议错误。"""


def _start(query: str, agent_key: str, timeout: int = 30) -> tuple[str, str]:
    headers = {"Content-Type": "application/json"}
    payload = {"agent_key": agent_key, "query": query}
    r = requests.post(AGENT_RUN_URL, headers=headers, json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if data.get("err_code") != 0:
        raise LLMClientError(f"agent/run err: {data.get('err_msg')}")
    sid = data.get("session_id")
    cid = data.get("chat_id")
    if not sid or not cid:
        raise LLMClientError("agent/run returned empty session_id/chat_id")
    return sid, cid


def _poll(session_id: str, chat_id: str, *, total_timeout: int = 60) -> str:
    headers = {"Content-Type": "application/json"}
    payload = {"session_id": session_id, "chat_id": chat_id}
    deadline = time.time() + total_timeout
    full_content = ""
    while True:
        if time.time() > deadline:
            raise TimeoutError(f"agent/poll timeout after {total_timeout}s")
        r = requests.post(AGENT_POLL_URL, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        if data.get("err_code") != 0:
            raise LLMClientError(f"agent/poll err: {data.get('err_msg')}")
        cur = data.get("content", "")
        if cur:
            full_content = cur
        if not data.get("has_more", False):
            return full_content
        interval_ms = data.get("interval", 1000)
        time.sleep(min(interval_ms / 1000.0, 2.0))


def classify_one(text: str, *, agent_key: Optional[str] = None,
                 timeout: int = 60, retry: int = 1) -> str:
    """同步调用 Agent，返回原始字符串回复。失败抛异常。"""
    key = agent_key or get_agent_key()
    last_exc: Optional[Exception] = None
    for attempt in range(retry + 1):
        try:
            sid, cid = _start(text, key)
            return _poll(sid, cid, total_timeout=timeout)
        except Exception as e:
            last_exc = e
            if attempt < retry:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise
    assert last_exc is not None
    raise last_exc
