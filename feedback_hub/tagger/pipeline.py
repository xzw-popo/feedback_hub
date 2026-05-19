"""打标流水线（spec §5.2）。

本模块包含：
- `aggregate_conversation_label`：纯函数，把多条消息标签聚合成会话标签（spec §4.3）
- `run_tagging`：编排函数——取待打标消息 → 规则+LLM → 写 message_label →
                 重算受影响 user_vid 的 conversation_id → 重算 conversation_label
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any, Callable, Optional

from feedback_hub import db
from feedback_hub.config import L1_PRIORITY, SEVERITY_PRIORITY
from feedback_hub.conversation import assign_conversation_ids
from feedback_hub.tagger import label_parser, rules


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


# ---------------------------------------------------------------------------
# run_tagging：编排
# ---------------------------------------------------------------------------

# LLM 调用器签名：(text) -> raw_reply_string；可被测试注入
LLMCallable = Callable[[str], str]


def _default_llm_caller() -> LLMCallable:
    """默认 LLM 调用器（懒加载，避免单测必须连真实环境）。"""
    from feedback_hub.tagger.llm_client import classify_one

    def call(text: str) -> str:
        return classify_one(text)
    return call


def _tag_one(text: str, llm_call: Optional[LLMCallable]) -> dict[str, Any]:
    """对一条消息打标：先规则，未命中走 LLM；LLM 失败回 '待定'。"""
    rule_hit = rules.apply_rules(text)
    if rule_hit is not None:
        return rule_hit

    # LLM 兜底
    if llm_call is None:
        # 没有可用 LLM：返回兜底"待定"
        return {
            "L1": "待定", "L2": [], "severity": "P3",
            "confidence": 0.0, "reason": "no_llm_available",
            "source": "llm", "rule_name": None,
        }
    try:
        reply = llm_call(text or "")
    except Exception as e:
        return {
            "L1": "待定", "L2": [], "severity": "P3",
            "confidence": 0.0, "reason": f"llm_error: {type(e).__name__}",
            "source": "llm", "rule_name": None,
        }
    parsed = label_parser.parse_llm_reply(reply)
    parsed["rule_name"] = None
    return parsed


def _l2_to_pipe(l2: Any) -> str:
    if isinstance(l2, list):
        return "|".join(str(x) for x in l2 if x)
    if l2 is None:
        return ""
    return str(l2)


def run_tagging(
    conn: sqlite3.Connection,
    *,
    llm_call: Optional[LLMCallable] = None,
    limit: Optional[int] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> dict[str, int]:
    """执行一轮打标流水线。返回统计字典。

    步骤（spec §5.2）：
        1. 取所有未打标的 feedback
        2. 逐条打标（规则优先 → LLM 兜底）→ upsert message_label
        3. 取本批涉及的所有 user_vid，对每个 user_vid 在数据库里重算 conversation_id
        4. 取本批涉及的所有 conversation_id，重新聚合 conversation_label

    Args:
        conn: 已 init_schema 的 sqlite 连接
        llm_call: 可注入的 LLM 调用器；为 None 时使用默认（真实远端）
        limit: 单次最多处理多少条；None=全部
        on_progress: 可选回调，参数 (done_count, total_count)
    """
    if llm_call is None:
        try:
            llm_call = _default_llm_caller()
        except Exception:
            llm_call = None  # 离线运行也应能跑（仅命中规则的部分被打标）

    untagged_rows = db.get_untagged_feedback(conn)
    if limit is not None:
        untagged_rows = untagged_rows[:limit]

    total = len(untagged_rows)
    stats = {
        "total": total, "tagged": 0,
        "by_rule": 0, "by_llm": 0, "pending": 0,
        "conversations_recomputed": 0,
        "user_vids_recomputed": 0,
    }
    if total == 0:
        return stats

    now = int(time.time())
    affected_feedback_ids: list[str] = []
    for i, row in enumerate(untagged_rows):
        text = row["text"]
        feedback_id = row["feedback_id"]
        result = _tag_one(text, llm_call)
        db.upsert_message_label(conn, {
            "feedback_id": feedback_id,
            "L1": result["L1"],
            "L2": _l2_to_pipe(result.get("L2")),
            "severity": result["severity"],
            "confidence": float(result.get("confidence") or 0.0),
            "reason": result.get("reason") or "",
            "source": result.get("source") or "rule",
            "rule_name": result.get("rule_name"),
            "tagged_at": now,
        })
        stats["tagged"] += 1
        if result.get("source") == "rule":
            stats["by_rule"] += 1
        elif result["L1"] == "待定":
            stats["pending"] += 1
            stats["by_llm"] += 1
        else:
            stats["by_llm"] += 1
        affected_feedback_ids.append(feedback_id)
        if on_progress:
            on_progress(i + 1, total)

    conn.commit()

    # ---- 重算 conversation_id（仅本批涉及的 user_vid 范围）----
    user_vids = db.get_distinct_user_vids_for_feedbacks(conn, affected_feedback_ids)
    affected_conv_ids: set[str] = set()
    for uv in user_vids:
        rows = db.get_feedback_by_user_vid(conn, uv)
        msgs = [{"feedback_id": r["feedback_id"],
                 "user_vid": r["user_vid"],
                 "ts_ms": r["ts_ms"]} for r in rows]
        out = assign_conversation_ids(msgs)
        for m in out:
            db.update_conversation_assignment(
                conn, m["feedback_id"], m["conversation_id"], m["msg_seq"],
            )
            affected_conv_ids.add(m["conversation_id"])
    stats["user_vids_recomputed"] = len(user_vids)

    # 还要把本批 feedback 自身的 conversation_id（可能从未排过的旧记录）也算入
    cur = conn.execute(
        f"SELECT DISTINCT conversation_id FROM feedback "
        f"WHERE feedback_id IN ({','.join('?'*len(affected_feedback_ids))})",
        tuple(affected_feedback_ids),
    )
    for r in cur.fetchall():
        affected_conv_ids.add(r[0])

    conn.commit()

    # ---- 重算 conversation_label ----
    for cid in affected_conv_ids:
        ml_rows = db.get_message_labels_for_conversation(conn, cid)
        rows_dicts = [{
            "feedback_id": r["feedback_id"],
            "L1": r["L1"], "L2": r["L2"], "severity": r["severity"],
            "confidence": r["confidence"], "reason": r["reason"],
            "ts_ms": r["ts_ms"], "msg_seq": r["msg_seq"],
            "user_vid": r["user_vid"], "appversion": r["appversion"],
            "channel": r["channel"],
        } for r in ml_rows]
        agg = aggregate_conversation_label(cid, rows_dicts)
        if agg is not None:
            db.upsert_conversation_label(conn, agg)
    stats["conversations_recomputed"] = len(affected_conv_ids)

    conn.commit()
    return stats
