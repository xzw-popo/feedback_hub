"""候选生成主编排（spec 阶段 2 §3.1 / §3.2）。

职责：
    1. 从 conversation_label + feedback + message_label 联合查询候选会话
    2. SQL 端用 CTE 拼接：
       - bug_convs：满足 L1=A.Bug + severity∈{P0,P1} + confidence≥0.7 + 7 天内
       - full_text_per_conv：每个 conv 的全部消息文本按 msg_seq 拼接
       - top_conf_msg：每个 conv 内 confidence 最高的那条 message
    3. 把 SQL 结果映射成 ConversationRow → 喂给 scorer.aggregate_and_score
    4. 返回 dict：{scanned, top_groups, all_groups_count}

只读数据库；push_log 写入由 cli.py 编排。
"""
from __future__ import annotations

import sqlite3

from feedback_hub.pusher import scorer
from feedback_hub.pusher.config_loader import load

SEVEN_DAYS_MS = 7 * 86_400_000


_CANDIDATE_SQL = """
WITH bug_convs AS (
    SELECT cl.conversation_id, cl.L1, cl.L2, cl.severity, cl.confidence,
           cl.reason, cl.msg_count, cl.first_ts_ms, cl.last_ts_ms,
           cl.user_vid, cl.appversion, cl.channel
    FROM conversation_label cl
    WHERE cl.L1 = 'A.Bug'
      AND cl.severity IN ('P0', 'P1')
      AND cl.confidence >= 0.7
      AND cl.last_ts_ms >= ?
),
full_text_per_conv AS (
    SELECT f.conversation_id,
           GROUP_CONCAT(f.text, ' || ') AS full_text
    FROM (SELECT conversation_id, text, msg_seq FROM feedback
          WHERE conversation_id IN (SELECT conversation_id FROM bug_convs)
          ORDER BY conversation_id, msg_seq) f
    GROUP BY f.conversation_id
),
top_conf_msg AS (
    SELECT t.feedback_id, t.conversation_id, t.text AS top_conf_text
    FROM (
        SELECT ml.feedback_id, f.conversation_id, f.text, ml.confidence,
               ROW_NUMBER() OVER (
                   PARTITION BY f.conversation_id
                   ORDER BY ml.confidence DESC, f.msg_seq ASC
               ) AS rn
        FROM message_label ml
        JOIN feedback f ON f.feedback_id = ml.feedback_id
        WHERE f.conversation_id IN (SELECT conversation_id FROM bug_convs)
    ) t
    WHERE t.rn = 1
)
SELECT bc.conversation_id, bc.L1, bc.L2, bc.severity, bc.confidence,
       bc.appversion, bc.last_ts_ms,
       ft.full_text,
       tc.feedback_id AS top_conf_feedback_id,
       tc.top_conf_text
FROM bug_convs bc
LEFT JOIN full_text_per_conv ft ON ft.conversation_id = bc.conversation_id
LEFT JOIN top_conf_msg tc ON tc.conversation_id = bc.conversation_id
ORDER BY bc.last_ts_ms DESC
"""


def _row_to_conv(row: sqlite3.Row) -> scorer.ConversationRow:
    return scorer.ConversationRow(
        conversation_id=row["conversation_id"],
        L1=row["L1"],
        L2=row["L2"] or "",
        severity=row["severity"],
        confidence=float(row["confidence"] or 0.0),
        appversion=row["appversion"],
        last_ts_ms=int(row["last_ts_ms"]),
        full_text=row["full_text"] or "",
        top_conf_text=row["top_conf_text"] or "",
        top_conf_feedback_id=row["top_conf_feedback_id"] or "",
    )


def generate_candidates(
    conn: sqlite3.Connection, *, now_ms: int,
) -> dict:
    """从数据库生成 Top 5 候选。

    Returns:
        {
            "scanned":          int,                      # 进入 scorer 的会话数
            "top_groups":       list[CandidateGroup],     # 至多 5 个
            "all_groups_count": int,                      # 截断前的桶数
        }
    """
    cfg = load()
    weights = cfg["scoring"]
    seven_days_ago_ms = now_ms - SEVEN_DAYS_MS

    cur = conn.execute(_CANDIDATE_SQL, (seven_days_ago_ms,))
    rows = cur.fetchall()
    convs = [_row_to_conv(r) for r in rows]

    all_groups = scorer.aggregate_and_score(
        convs, now_ms=now_ms, weights=weights,
    )
    top = scorer.pick_top5(all_groups)

    return {
        "scanned": len(convs),
        "top_groups": top,
        "all_groups_count": len(all_groups),
    }
