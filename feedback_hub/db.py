"""SQLite 连接管理 + 三类 upsert helper。

设计原则：
- 用 stdlib `sqlite3`，不引 ORM
- helper 函数都接 connection（外部决定事务边界），不在内部 commit
  （上层 pipeline / API 自行 commit，便于批量写入）
- INSERT OR IGNORE 用于"幂等插入"语义（feedback 主键冲突时跳过）
- INSERT OR REPLACE 用于"覆盖打标"语义（message_label / conversation_label 重打标）
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from feedback_hub import config


# 字段顺序与 schema.sql 保持一致
FEEDBACK_COLUMNS: tuple[str, ...] = (
    "feedback_id", "conversation_id", "msg_seq", "channel", "ts_ms",
    "platform", "appversion", "user_vid", "keyboard_source", "device_name",
    "channelid", "enginever", "msgtype", "text", "tags", "raw_json", "pulled_at",
)
MESSAGE_LABEL_COLUMNS: tuple[str, ...] = (
    "feedback_id", "L1", "L2", "severity", "confidence",
    "reason", "source", "rule_name", "tagged_at",
)
CONVERSATION_LABEL_COLUMNS: tuple[str, ...] = (
    "conversation_id", "L1", "L2", "severity", "confidence", "reason", "source",
    "msg_count", "first_ts_ms", "last_ts_ms",
    "user_vid", "appversion", "channel", "aggregated_at",
)


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """打开 sqlite 连接，启用外键 + Row 工厂。"""
    p = Path(db_path) if db_path is not None else config.DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """加载 schema.sql。重复执行幂等。"""
    sql = config.SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)
    conn.commit()


# ----------------------------- feedback ---------------------------------

def upsert_feedback(conn: sqlite3.Connection, row: dict[str, Any]) -> bool:
    """插入一条 feedback。主键冲突时跳过（幂等）。
    返回 True 表示新插入，False 表示已存在被跳过。
    """
    placeholders = ", ".join("?" * len(FEEDBACK_COLUMNS))
    cols = ", ".join(FEEDBACK_COLUMNS)
    vals = tuple(row.get(c) for c in FEEDBACK_COLUMNS)
    cur = conn.execute(
        f"INSERT OR IGNORE INTO feedback ({cols}) VALUES ({placeholders})", vals
    )
    return cur.rowcount > 0


def update_conversation_assignment(
    conn: sqlite3.Connection, feedback_id: str,
    conversation_id: str, msg_seq: int,
) -> None:
    """更新某条 feedback 的会话归属（聚合算法重算后调用）。"""
    conn.execute(
        "UPDATE feedback SET conversation_id = ?, msg_seq = ? WHERE feedback_id = ?",
        (conversation_id, msg_seq, feedback_id),
    )


def get_feedback_by_user_vid(
    conn: sqlite3.Connection, user_vid: str,
) -> list[sqlite3.Row]:
    """按 user_vid 取所有消息，按 ts_ms 升序——给会话聚合用。"""
    cur = conn.execute(
        "SELECT * FROM feedback WHERE user_vid = ? ORDER BY ts_ms ASC, feedback_id ASC",
        (user_vid,),
    )
    return cur.fetchall()


def get_distinct_user_vids_for_feedbacks(
    conn: sqlite3.Connection, feedback_ids: list[str],
) -> list[str]:
    """取一批 feedback_id 涉及的所有唯一 user_vid。"""
    if not feedback_ids:
        return []
    placeholders = ", ".join("?" * len(feedback_ids))
    cur = conn.execute(
        f"SELECT DISTINCT user_vid FROM feedback "
        f"WHERE feedback_id IN ({placeholders}) AND user_vid IS NOT NULL",
        tuple(feedback_ids),
    )
    return [r[0] for r in cur.fetchall()]


# ----------------------------- message_label ----------------------------

def upsert_message_label(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    """打标结果写库；主键冲突时覆盖（重打标语义）。"""
    placeholders = ", ".join("?" * len(MESSAGE_LABEL_COLUMNS))
    cols = ", ".join(MESSAGE_LABEL_COLUMNS)
    vals = tuple(row.get(c) for c in MESSAGE_LABEL_COLUMNS)
    conn.execute(
        f"INSERT OR REPLACE INTO message_label ({cols}) VALUES ({placeholders})", vals
    )


def get_untagged_feedback(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """取所有未打标的 feedback（左联 message_label 为 NULL）。"""
    cur = conn.execute(
        "SELECT f.* FROM feedback f "
        "LEFT JOIN message_label ml ON f.feedback_id = ml.feedback_id "
        "WHERE ml.feedback_id IS NULL "
        "ORDER BY f.ts_ms ASC"
    )
    return cur.fetchall()


def get_message_labels_for_conversation(
    conn: sqlite3.Connection, conversation_id: str,
) -> list[sqlite3.Row]:
    """取一个会话的全部 message_label（带 ts_ms 用于排序）。"""
    cur = conn.execute(
        "SELECT ml.*, f.ts_ms, f.user_vid, f.appversion, f.channel, f.msg_seq "
        "FROM message_label ml "
        "JOIN feedback f ON f.feedback_id = ml.feedback_id "
        "WHERE f.conversation_id = ? "
        "ORDER BY f.msg_seq ASC",
        (conversation_id,),
    )
    return cur.fetchall()


# --------------------------- conversation_label -------------------------

def upsert_conversation_label(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    """会话级标签写库；主键冲突时覆盖（重算语义）。"""
    placeholders = ", ".join("?" * len(CONVERSATION_LABEL_COLUMNS))
    cols = ", ".join(CONVERSATION_LABEL_COLUMNS)
    vals = tuple(row.get(c) for c in CONVERSATION_LABEL_COLUMNS)
    conn.execute(
        f"INSERT OR REPLACE INTO conversation_label ({cols}) VALUES ({placeholders})",
        vals,
    )


def delete_conversation_label(conn: sqlite3.Connection, conversation_id: str) -> None:
    """删除会话标签（重算前清理用，可选）。"""
    conn.execute(
        "DELETE FROM conversation_label WHERE conversation_id = ?",
        (conversation_id,),
    )


# ----------------------------- push_log ---------------------------------

# 顺序与 schema.sql 中 push_log 表的字段顺序保持一致（不含自增 id）
PUSH_LOG_COLUMNS: tuple[str, ...] = (
    "push_date", "rank", "signature",
    "group_id", "primary_l2", "major_version",
    "representative_conversation_id", "representative_feedback_id",
    "score", "dup_count", "p0_count", "cross_version",
    "affected_versions", "representative_text",
    "created_at", "delivered_at", "is_empty",
)


def insert_push_log(conn: sqlite3.Connection, row: dict[str, Any]) -> int:
    """插入一条推送日志，返回自增 id。"""
    placeholders = ", ".join("?" * len(PUSH_LOG_COLUMNS))
    cols = ", ".join(PUSH_LOG_COLUMNS)
    vals = tuple(row.get(c) for c in PUSH_LOG_COLUMNS)
    cur = conn.execute(
        f"INSERT INTO push_log ({cols}) VALUES ({placeholders})", vals,
    )
    return int(cur.lastrowid)


def update_push_log_delivered(
    conn: sqlite3.Connection, push_log_id: int, delivered_at: int,
) -> None:
    """标记一条 push_log 为已送达。"""
    conn.execute(
        "UPDATE push_log SET delivered_at = ? WHERE id = ?",
        (delivered_at, push_log_id),
    )
