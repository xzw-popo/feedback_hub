"""数据库连接管理 + 三类 upsert helper。

设计原则：
- 支持 SQLite（本地/内网）和 MySQL（CloudBase）两种后端
- 通过 DB_MODE 环境变量切换：sqlite（默认）/ mysql
- helper 函数都接 connection（外部决定事务边界），不在内部 commit
  （上层 pipeline / API 自行 commit，便于批量写入）
- SQLite: INSERT OR IGNORE / INSERT OR REPLACE
- MySQL:  INSERT IGNORE / ON DUPLICATE KEY UPDATE
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from feedback_hub import config


# ---------- 类型别名 ----------

# 统一连接类型：sqlite3.Connection 或 _MySQLConnection
# 供上层模块（pusher/push_log, tagger/pipeline 等）使用
Connection = Any


# ---------- 方言抽象 ----------

def _db_mode() -> str:
    """返回当前数据库模式：'sqlite' 或 'mysql'。"""
    return os.environ.get("DB_MODE", "sqlite").lower()


def _ph(n: int) -> str:
    """占位符：SQLite 用 '?'，MySQL 用 '%s'。"""
    if _db_mode() == "mysql":
        return ", ".join(["%s"] * n)
    return ", ".join(["?"] * n)


# ---------- 字段定义（两种后端共享） ----------

# 字段顺序与 schema.sql / schema_mysql.sql 保持一致
FEEDBACK_COLUMNS: tuple[str, ...] = (
    "feedback_id", "conversation_id", "msg_seq", "channel", "ts_ms",
    "platform", "appversion", "user_vid", "service_vid", "external_chat_url",
    "keyboard_source", "device_name", "channelid", "enginever", "msgtype",
    "text", "tags", "raw_json", "pulled_at",
)
MESSAGE_LABEL_COLUMNS: tuple[str, ...] = (
    "feedback_id", "L1", "L2", "severity", "confidence",
    "reason", "source", "rule_name", "tagged_at",
)
CONVERSATION_LABEL_COLUMNS: tuple[str, ...] = (
    "conversation_id", "L1", "L2", "severity", "confidence", "reason", "source",
    "msg_count", "first_ts_ms", "last_ts_ms",
    "user_vid", "appversion", "channel", "service_vid", "external_chat_url",
    "aggregated_at",
)
PUSH_LOG_COLUMNS: tuple[str, ...] = (
    "push_date", "rank", "signature",
    "group_id", "primary_l2", "major_version",
    "representative_conversation_id", "representative_feedback_id",
    "score", "dup_count", "p0_count", "cross_version",
    "affected_versions", "representative_text",
    "created_at", "delivered_at", "is_empty",
)


# ---------- SQLite 连接 ----------

# REGEXP 单条文本最大匹配长度。超长文本（崩溃日志/直播口水等）配合 LLM 生成的
# 含 .* 正则会触发灾难性回溯（catastrophic backtracking），曾实测单条卡死 80s+，
# 远超前端 60s 超时。截断后再匹配，保证最坏情况也在毫秒级。
# 与 search/api.py 的子查询长度过滤共用 config.SEARCH_REGEXP_MAX_TEXT_LEN。
_REGEXP_MAX_LEN: int = config.SEARCH_REGEXP_MAX_TEXT_LEN


def _sqlite_regexp(pattern: str, string: str) -> int:
    """SQLite REGEXP 回调：用 re.search 判断 pattern 是否匹配 string。

    string 截断到 _REGEXP_MAX_LEN，防止超长文本触发正则回溯爆炸。
    """
    if pattern is None or string is None:
        return 0
    if len(string) > _REGEXP_MAX_LEN:
        string = string[:_REGEXP_MAX_LEN]
    return 1 if re.search(pattern, string, re.IGNORECASE) else 0


def _connect_sqlite(db_path: Path | str | None = None) -> sqlite3.Connection:
    """打开 SQLite 连接，启用外键 + Row 工厂 + REGEXP 支持。"""
    p = Path(db_path) if db_path is not None else config.DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.create_function("REGEXP", 2, _sqlite_regexp)
    return conn


# ---------- MySQL 连接 ----------

class _MySQLConnection:
    """包装 pymysql.Connection，使其 API 兼容 sqlite3.Connection。

    核心差异：
    - sqlite3.Connection.execute() 返回 Cursor
    - pymysql.Connection 没有 .execute()，必须先 conn.cursor().execute()
    - sqlite3 支持 with conn 上下文管理器自动 commit/rollback

    本类在 pymysql 之上添加 .execute() 代理方法，
    使 api.py 中的 conn.execute(sql, params).fetchone() 模式在两种后端下统一。
    """

    def __init__(self, conn: Any):
        self._conn = conn

    def execute(self, sql: str, params: Any = None) -> Any:
        """创建 cursor → execute → 返回 cursor（与 sqlite3 行为一致）。"""
        cur = self._conn.cursor()
        cur.execute(sql, params)
        return cur

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    def cursor(self) -> Any:
        return self._conn.cursor()

    @property
    def placeholder(self) -> str:
        """占位符：MySQL 用 %s。"""
        return "%s"

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        self._conn.close()
        return False


def _connect_mysql() -> _MySQLConnection:
    """打开 MySQL 连接（pymysql），返回兼容 sqlite3 的包装对象。

    环境变量：
      MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE
    """
    import pymysql

    raw = pymysql.connect(
        host=os.environ.get("MYSQL_HOST", "localhost"),
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ.get("MYSQL_USER", "root"),
        password=os.environ.get("MYSQL_PASSWORD", ""),
        database=os.environ.get("MYSQL_DATABASE", "feedback_hub"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )
    return _MySQLConnection(raw)


# ---------- 统一连接入口 ----------

def connect(db_path: Path | str | None = None) -> Any:
    """根据 DB_MODE 返回 SQLite 或 MySQL 连接。"""
    if _db_mode() == "mysql":
        return _connect_mysql()
    return _connect_sqlite(db_path)


# ---------- Schema 初始化 ----------

def init_schema(conn: Any) -> None:
    """加载建表 SQL。重复执行幂等。"""
    mode = _db_mode()
    if mode == "mysql":
        schema_path = config.PKG_DIR / "schema_mysql.sql"
    else:
        schema_path = config.SCHEMA_PATH
    sql = schema_path.read_text(encoding="utf-8")

    if mode == "mysql":
        # MySQL: pymysql 没有 executescript，逐条执行
        # 用 ';' 分割，忽略空语句和注释
        for stmt in sql.split(";"):
            stmt = stmt.strip()
            if stmt and not stmt.startswith("--"):
                conn.execute(stmt)
        conn.commit()
    else:
        conn.executescript(sql)
        conn.commit()

    _ensure_compat_columns(conn)
    _backfill_external_chat_fields(conn)


def _ensure_compat_columns(conn: Any) -> None:
    """给旧库补充新列；CREATE TABLE IF NOT EXISTS 不会更新既有表结构。"""
    additions = {
        "feedback": {
            "service_vid": "BIGINT" if _db_mode() == "mysql" else "INTEGER",
            "external_chat_url": "TEXT",
        },
        "conversation_label": {
            "service_vid": "BIGINT" if _db_mode() == "mysql" else "INTEGER",
            "external_chat_url": "TEXT",
        },
    }
    for table, cols in additions.items():
        existing = _table_columns(conn, table)
        for col, ddl_type in cols.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl_type}")
    conn.commit()


def _table_columns(conn: Any, table: str) -> set[str]:
    if _db_mode() == "mysql":
        rows = conn.execute(f"SHOW COLUMNS FROM {table}").fetchall()
        return {r["Field"] if isinstance(r, dict) else r[0] for r in rows}
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def build_external_chat_url(
    channel: str | None,
    service_vid: int | str | None,
    user_vid: str | None,
) -> str | None:
    """拼 wrfeedback 原始会话 URL。缺 user_vid 时无法定位用户会话。"""
    if not user_vid:
        return None
    channel = channel or config.DEFAULT_CHANNEL
    service_vid = service_vid or config.DEFAULT_SERVICE_VID
    params = urlencode({
        "channel": channel,
        "serviceVid": service_vid,
        "userVid": user_vid,
    })
    return f"https://wrfeedback.weread.woa.com/chat?{params}"


def _service_vid_from_raw(raw_json: Any) -> int | None:
    if not raw_json:
        return None
    try:
        raw = json.loads(raw_json)
    except (TypeError, ValueError):
        return None
    service_vid = raw.get("service_vid")
    if service_vid is None:
        return None
    try:
        return int(service_vid)
    except (TypeError, ValueError):
        return None


def _backfill_external_chat_fields(conn: Any) -> None:
    """旧数据从 raw_json.service_vid 回填，保证新增列可直接查询。"""
    rows = conn.execute(
        "SELECT feedback_id, channel, user_vid, raw_json, service_vid, external_chat_url "
        "FROM feedback WHERE user_vid IS NOT NULL "
        "AND (service_vid IS NULL OR external_chat_url IS NULL)"
    ).fetchall()
    ph = "%s" if _db_mode() == "mysql" else "?"
    for row in rows:
        feedback_id = _row_get(row, "feedback_id")
        service_vid = _row_get(row, "service_vid") or _service_vid_from_raw(_row_get(row, "raw_json"))
        service_vid = service_vid or config.DEFAULT_SERVICE_VID
        external_chat_url = _row_get(row, "external_chat_url") or build_external_chat_url(
            _row_get(row, "channel"),
            service_vid,
            _row_get(row, "user_vid"),
        )
        conn.execute(
            f"UPDATE feedback SET service_vid = {ph}, external_chat_url = {ph} "
            f"WHERE feedback_id = {ph}",
            (service_vid, external_chat_url, feedback_id),
        )

    conv_rows = conn.execute(
        "SELECT cl.conversation_id, f.channel, f.user_vid, f.service_vid, f.external_chat_url "
        "FROM conversation_label cl "
        "JOIN feedback f ON f.conversation_id = cl.conversation_id AND f.msg_seq = 0 "
        "WHERE cl.service_vid IS NULL OR cl.external_chat_url IS NULL"
    ).fetchall()
    for row in conv_rows:
        service_vid = _row_get(row, "service_vid") or config.DEFAULT_SERVICE_VID
        external_chat_url = _row_get(row, "external_chat_url") or build_external_chat_url(
            _row_get(row, "channel"),
            service_vid,
            _row_get(row, "user_vid"),
        )
        conn.execute(
            f"UPDATE conversation_label SET service_vid = {ph}, external_chat_url = {ph} "
            f"WHERE conversation_id = {ph}",
            (service_vid, external_chat_url, _row_get(row, "conversation_id")),
        )
    conn.commit()


# ---------- Row 兼容层 ----------

def _row_get(row: Any, key: str, default: Any = None) -> Any:
    """兼容 sqlite3.Row 和 pymysql DictCursor 的字段读取。"""
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


# ----------------------------- feedback ---------------------------------

def upsert_feedback(conn: Any, row: dict[str, Any]) -> bool:
    """插入一条 feedback。主键冲突时跳过（幂等）。
    返回 True 表示新插入，False 表示已存在被跳过。
    """
    cols = ", ".join(FEEDBACK_COLUMNS)
    vals = tuple(row.get(c) for c in FEEDBACK_COLUMNS)
    if _db_mode() == "mysql":
        sql = f"INSERT IGNORE INTO feedback ({cols}) VALUES ({_ph(len(FEEDBACK_COLUMNS))})"
    else:
        sql = f"INSERT OR IGNORE INTO feedback ({cols}) VALUES ({_ph(len(FEEDBACK_COLUMNS))})"
    cur = conn.execute(sql, vals)
    return cur.rowcount > 0


def update_conversation_assignment(
    conn: Any, feedback_id: str,
    conversation_id: str, msg_seq: int,
) -> None:
    """更新某条 feedback 的会话归属（聚合算法重算后调用）。"""
    conn.execute(
        "UPDATE feedback SET conversation_id = %s, msg_seq = %s WHERE feedback_id = %s"
        if _db_mode() == "mysql" else
        "UPDATE feedback SET conversation_id = ?, msg_seq = ? WHERE feedback_id = ?",
        (conversation_id, msg_seq, feedback_id),
    )


def get_feedback_by_user_vid(
    conn: Any, user_vid: str,
) -> list[Any]:
    """按 user_vid 取所有消息，按 ts_ms 升序——给会话聚合用。"""
    sql = ("SELECT * FROM feedback WHERE user_vid = "
           + ("%s" if _db_mode() == "mysql" else "?")
           + " ORDER BY ts_ms ASC, feedback_id ASC")
    cur = conn.execute(sql, (user_vid,))
    return cur.fetchall()


def get_distinct_user_vids_for_feedbacks(
    conn: Any, feedback_ids: list[str],
) -> list[str]:
    """取一批 feedback_id 涉及的所有唯一 user_vid。"""
    if not feedback_ids:
        return []
    ph = _ph(len(feedback_ids))
    cur = conn.execute(
        f"SELECT DISTINCT user_vid FROM feedback "
        f"WHERE feedback_id IN ({ph}) AND user_vid IS NOT NULL",
        tuple(feedback_ids),
    )
    rows = cur.fetchall()
    if _db_mode() == "mysql":
        return [r["user_vid"] for r in rows]
    return [r[0] for r in rows]


# ----------------------------- message_label ----------------------------

def upsert_message_label(conn: Any, row: dict[str, Any]) -> None:
    """打标结果写库；主键冲突时覆盖（重打标语义）。"""
    cols = ", ".join(MESSAGE_LABEL_COLUMNS)
    vals = tuple(row.get(c) for c in MESSAGE_LABEL_COLUMNS)
    if _db_mode() == "mysql":
        # MySQL: ON DUPLICATE KEY UPDATE 覆盖所有字段
        update_cols = ", ".join(f"{c}=VALUES({c})" for c in MESSAGE_LABEL_COLUMNS)
        sql = (f"INSERT INTO message_label ({cols}) VALUES ({_ph(len(MESSAGE_LABEL_COLUMNS))}) "
               f"ON DUPLICATE KEY UPDATE {update_cols}")
    else:
        sql = f"INSERT OR REPLACE INTO message_label ({cols}) VALUES ({_ph(len(MESSAGE_LABEL_COLUMNS))})"
    conn.execute(sql, vals)


def get_untagged_feedback(conn: Any) -> list[Any]:
    """取所有未打标的 feedback（左联 message_label 为 NULL）。"""
    cur = conn.execute(
        "SELECT f.* FROM feedback f "
        "LEFT JOIN message_label ml ON f.feedback_id = ml.feedback_id "
        "WHERE ml.feedback_id IS NULL "
        "ORDER BY f.ts_ms ASC"
    )
    return cur.fetchall()


def get_message_labels_for_conversation(
    conn: Any, conversation_id: str,
) -> list[Any]:
    """取一个会话的全部 message_label（带 ts_ms 用于排序）。"""
    ph = "%s" if _db_mode() == "mysql" else "?"
    cur = conn.execute(
        f"SELECT ml.*, f.ts_ms, f.user_vid, f.appversion, f.channel, f.msg_seq, "
        f"f.service_vid, f.external_chat_url "
        f"FROM message_label ml "
        f"JOIN feedback f ON f.feedback_id = ml.feedback_id "
        f"WHERE f.conversation_id = {ph} "
        f"ORDER BY f.msg_seq ASC",
        (conversation_id,),
    )
    return cur.fetchall()


# --------------------------- conversation_label -------------------------

def upsert_conversation_label(conn: Any, row: dict[str, Any]) -> None:
    """会话级标签写库；主键冲突时覆盖（重算语义）。"""
    cols = ", ".join(CONVERSATION_LABEL_COLUMNS)
    vals = tuple(row.get(c) for c in CONVERSATION_LABEL_COLUMNS)
    if _db_mode() == "mysql":
        update_cols = ", ".join(f"{c}=VALUES({c})" for c in CONVERSATION_LABEL_COLUMNS)
        sql = (f"INSERT INTO conversation_label ({cols}) VALUES ({_ph(len(CONVERSATION_LABEL_COLUMNS))}) "
               f"ON DUPLICATE KEY UPDATE {update_cols}")
    else:
        sql = f"INSERT OR REPLACE INTO conversation_label ({cols}) VALUES ({_ph(len(CONVERSATION_LABEL_COLUMNS))})"
    conn.execute(sql, vals)


def delete_conversation_label(conn: Any, conversation_id: str) -> None:
    """删除会话标签（重算前清理用，可选）。"""
    ph = "%s" if _db_mode() == "mysql" else "?"
    conn.execute(
        f"DELETE FROM conversation_label WHERE conversation_id = {ph}",
        (conversation_id,),
    )


# ----------------------------- push_log ---------------------------------

def insert_push_log(conn: Any, row: dict[str, Any]) -> int:
    """插入一条推送日志，返回自增 id。"""
    cols = ", ".join(PUSH_LOG_COLUMNS)
    vals = tuple(row.get(c) for c in PUSH_LOG_COLUMNS)
    sql = f"INSERT INTO push_log ({cols}) VALUES ({_ph(len(PUSH_LOG_COLUMNS))})"
    cur = conn.execute(sql, vals)
    return int(cur.lastrowid)


def update_push_log_delivered(
    conn: Any, push_log_id: int, delivered_at: int,
) -> None:
    """标记一条 push_log 为已送达。"""
    if _db_mode() == "mysql":
        conn.execute(
            "UPDATE push_log SET delivered_at = %s WHERE id = %s",
            (delivered_at, push_log_id),
        )
    else:
        conn.execute(
            "UPDATE push_log SET delivered_at = ? WHERE id = ?",
            (delivered_at, push_log_id),
        )
