"""打标流水线（spec §5.2）。

本模块包含两类内容：
- `aggregate_conversation_label`：纯函数，把多条消息标签聚合成会话标签（spec §4.3）
- `run_tagging`：编排函数（Task 10 加入）

把"聚合"独立成纯函数，便于单元测试。
"""
from __future__ import annotations

import time
from typing import Any, Optional

from feedback_hub.config import L1_PRIORITY, SEVERITY_PRIORITY


def aggregate_conversation_label(
    conversation_id: str, rows: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """把一个会话的多条 message_label 聚合成 conversation_label。

    输入 rows 每条至少含：
        feedback_id, L1, L2, severity, confidence, reason,
        ts_ms, msg_seq, user_vid, appversion, channel

    spec §4.3 规则：
        L1   = max by L1_PRIORITY
        L2   = union of all
        severity = max by SEVERITY_PRIORITY
        confidence = confidence of the chosen-L1-row（多条同 L1 取 confidence 最高）
        reason   = reason of that chosen row
        msg_count / first_ts / last_ts = SQL aggregates
        user_vid / appversion / channel = msg_seq == 0 那条

    返回 dict（或 rows 为空时 None）。
    """
    if not rows:
        return None

    # L1：按优先级最高
    l1_priority = max(L1_PRIORITY.get(r["L1"], 0) for r in rows)
    l1 = next(k for k, v in L1_PRIORITY.items() if v == l1_priority)

    # 同 L1 的所有候选里，选 confidence 最高那条作为 reason/confidence 来源
    same_l1 = [r for r in rows if r["L1"] == l1]
    chosen = max(same_l1, key=lambda r: r.get("confidence") or 0.0)
    confidence = float(chosen.get("confidence") or 0.0)
    reason = chosen.get("reason") or ""

    # severity：按优先级最高
    sev_priority = max(SEVERITY_PRIORITY.get(r["severity"], 0) for r in rows)
    severity = next(k for k, v in SEVERITY_PRIORITY.items() if v == sev_priority)

    # L2 union
    l2_set: set[str] = set()
    for r in rows:
        raw = r.get("L2") or ""
        for part in str(raw).split("|"):
            part = part.strip()
            if part:
                l2_set.add(part)
    l2 = "|".join(sorted(l2_set))

    msg_count = len(rows)
    first_ts_ms = min(int(r["ts_ms"]) for r in rows)
    last_ts_ms = max(int(r["ts_ms"]) for r in rows)

    # 取 msg_seq == 0 那条；找不到时退化到最早 ts 那条
    seq0 = [r for r in rows if int(r.get("msg_seq", 0)) == 0]
    meta = seq0[0] if seq0 else min(rows, key=lambda r: int(r["ts_ms"]))

    return {
        "conversation_id": conversation_id,
        "L1": l1,
        "L2": l2,
        "severity": severity,
        "confidence": confidence,
        "reason": reason,
        "source": "aggregated",
        "msg_count": msg_count,
        "first_ts_ms": first_ts_ms,
        "last_ts_ms": last_ts_ms,
        "user_vid": meta.get("user_vid"),
        "appversion": meta.get("appversion"),
        "channel": meta.get("channel"),
        "aggregated_at": int(time.time()),
    }
