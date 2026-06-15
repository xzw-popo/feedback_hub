"""push_log 表的业务语义包装层（spec 阶段 2 §5.1 / §5.2）。

`db.py` 是底层（行级 insert/update）；这里是业务层（CandidateGroup → 多行）。
"""
from __future__ import annotations

from typing import Any, List

from feedback_hub import db
from feedback_hub.db import Connection as DBConnection
from feedback_hub.pusher import scorer


def _serialize_versions(versions: dict) -> str:
    """{'5.4.0': 3, '5.4.1': 9} → '5.4.1:9|5.4.0:3'（按 count 降序）。"""
    items = sorted(versions.items(), key=lambda kv: (-kv[1], kv[0]))
    return "|".join(f"{v}:{c}" for v, c in items)


def _group_to_row(rank: int, group: scorer.CandidateGroup,
                  push_date: str, created_at: int) -> dict:
    g_id, primary_l2, major_ver = group.signature
    return {
        "push_date": push_date,
        "rank": rank,
        "signature": f"{g_id}|{primary_l2}|{major_ver}",
        "group_id": g_id,
        "primary_l2": primary_l2,
        "major_version": major_ver,
        "representative_conversation_id": group.representative_conv_id,
        "representative_feedback_id": group.representative_feedback_id,
        "score": group.score,
        "dup_count": group.dup_count,
        "p0_count": group.p0_count,
        "cross_version": group.cross_version,
        "affected_versions": _serialize_versions(group.affected_versions),
        "representative_text": group.representative_text,
        "created_at": created_at,
        "delivered_at": None,
        "is_empty": 0,
    }


def _empty_placeholder_row(push_date: str, created_at: int) -> dict:
    return {
        "push_date": push_date,
        "rank": 0,
        "signature": None,
        "group_id": None,
        "primary_l2": None,
        "major_version": None,
        "representative_conversation_id": None,
        "representative_feedback_id": None,
        "score": None,
        "dup_count": None,
        "p0_count": None,
        "cross_version": None,
        "affected_versions": None,
        "representative_text": None,
        "created_at": created_at,
        "delivered_at": None,
        "is_empty": 1,
    }


def save_top_groups(
    conn: DBConnection, *,
    push_date: str,
    top_groups: List[scorer.CandidateGroup],
    created_at: int,
) -> List[int]:
    """把 Top 5 落 push_log；候选为空时插一条 is_empty=1 占位行。

    Returns:
        本次插入的 push_log id 列表（顺序与 top_groups 一致；空候选时长度=1）
    """
    ids: List[int] = []
    if not top_groups:
        pid = db.insert_push_log(
            conn, _empty_placeholder_row(push_date, created_at),
        )
        ids.append(pid)
    else:
        for i, g in enumerate(top_groups):
            pid = db.insert_push_log(
                conn, _group_to_row(i + 1, g, push_date, created_at),
            )
            ids.append(pid)
    conn.commit()
    return ids


def mark_delivered(
    conn: DBConnection, push_log_ids: List[int], *, delivered_at: int,
) -> None:
    """批量把一组 push_log 标记为已送达。"""
    for pid in push_log_ids:
        db.update_push_log_delivered(conn, pid, delivered_at)
    conn.commit()
