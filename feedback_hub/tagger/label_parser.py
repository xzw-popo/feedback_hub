"""LLM 回复 JSON 解析与归一化。

容错策略：
1. ```json ... ``` 围栏优先
2. 退化到 ``` ... ``` 围栏
3. 退化到第一个 {...} 块
4. 全部失败 → L1='待定' + reason='parse_error: ...'

字段归一化：
- L1 不在枚举 → '待定'
- L2 非数组 / 含非法值 → 过滤为合法子集
- severity 不在枚举 → 'P3'
- confidence 强制 [0, 1]
- reason 截断到 80 字符
"""
from __future__ import annotations

import json
import re

from feedback_hub.config import L1_VALUES, L2_VALUES, SEVERITY_VALUES

_FENCE_JSON_RE = re.compile(r"```json\s*(.+?)\s*```", re.DOTALL | re.IGNORECASE)
_FENCE_ANY_RE = re.compile(r"```\s*(.+?)\s*```", re.DOTALL)
_BRACE_RE = re.compile(r"\{[\s\S]*\}")


def _extract_json_str(reply: str) -> str | None:
    if not reply:
        return None
    m = _FENCE_JSON_RE.search(reply)
    if m:
        return m.group(1).strip()
    m = _FENCE_ANY_RE.search(reply)
    if m:
        cand = m.group(1).strip()
        if cand.startswith("{"):
            return cand
    m = _BRACE_RE.search(reply)
    if m:
        return m.group(0).strip()
    return None


def _fallback(reason: str) -> dict:
    return {
        "L1": "待定",
        "L2": [],
        "severity": "P3",
        "confidence": 0.0,
        "reason": reason,
        "source": "llm",
    }


def parse_llm_reply(reply: str) -> dict:
    raw = _extract_json_str(reply or "")
    if raw is None:
        return _fallback("parse_error: no_json_found")
    try:
        obj = json.loads(raw)
    except Exception as e:
        return _fallback(f"parse_error: {type(e).__name__}")
    if not isinstance(obj, dict):
        return _fallback("parse_error: not_object")

    l1 = obj.get("L1") or "待定"
    if l1 not in L1_VALUES:
        l1 = "待定"

    raw_l2 = obj.get("L2") or []
    if not isinstance(raw_l2, list):
        l2 = []
    else:
        l2 = [x for x in raw_l2 if isinstance(x, str) and x in L2_VALUES]

    sev = obj.get("severity") or "P3"
    if sev not in SEVERITY_VALUES:
        sev = "P3"

    try:
        conf = float(obj.get("confidence", 0.0))
    except Exception:
        conf = 0.0
    conf = max(0.0, min(1.0, conf))

    reason = obj.get("reason") or ""
    if not isinstance(reason, str):
        reason = str(reason)
    reason = reason.strip()[:80]

    return {
        "L1": l1,
        "L2": l2,
        "severity": sev,
        "confidence": conf,
        "reason": reason or "llm",
        "source": "llm",
    }
