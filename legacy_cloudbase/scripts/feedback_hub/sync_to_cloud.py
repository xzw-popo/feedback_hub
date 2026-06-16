"""SQLite → MySQL 增量同步脚本。

用途：在内网环境运行，将本地 SQLite 的数据同步到 CloudBase MySQL，
供 CloudRun 上的只读 API 查询使用。

同步策略：
  - feedback / message_label / conversation_label：基于主键 UPSERT（INSERT ... ON DUPLICATE KEY UPDATE）
  - push_log：基于 id 范围增量同步
  - 全量同步或增量同步（--full 全量，默认增量）

环境变量：
  MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE
  SQLITE_PATH（默认 feedback_hub/data/feedback.db）

用法：
  python -m feedback_hub.sync_to_cloud
  python -m feedback_hub.sync_to_cloud --full
  python -m feedback_hub.sync_to_cloud --sqlite-path /path/to/feedback.db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

# 延迟导入 pymysql，避免内网环境无此依赖时报错


def _connect_sqlite(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _connect_mysql() -> Any:
    """通过 db 模块连接 MySQL，返回 _MySQLConnection 包装对象。"""
    from feedback_hub.db import _connect_mysql as db_mysql_connect
    return db_mysql_connect()


def _env(key: str, default: str = "") -> str:
    import os
    return os.environ.get(key, default)


def _upsert_mysql(
    mysql_conn: Any, table: str, columns: tuple[str, ...],
    rows: list[sqlite3.Row], batch_size: int = 500,
) -> int:
    """将一批 SQLite rows 写入 MySQL（UPSERT）。返回写入行数。"""
    if not rows:
        return 0

    cols = ", ".join(columns)
    ph = ", ".join(["%s"] * len(columns))
    update_cols = ", ".join(f"{c}=VALUES({c})" for c in columns)
    sql = (f"INSERT INTO {table} ({cols}) VALUES ({ph}) "
           f"ON DUPLICATE KEY UPDATE {update_cols}")

    count = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        vals = [tuple(row[c] for c in columns) for row in batch]
        with mysql_conn.cursor() as cur:
            cur.executemany(sql, vals)
        count += len(batch)
    mysql_conn.commit()
    return count


def sync_feedback(sqlite_conn: sqlite3.Connection, mysql_conn: Any, full: bool) -> int:
    """同步 feedback 表。"""
    if full:
        cur = sqlite_conn.execute("SELECT * FROM feedback ORDER BY ts_ms ASC")
    else:
        # 增量：只同步最近 7 天的数据（留余量避免边界遗漏）
        cur = sqlite_conn.execute(
            "SELECT * FROM feedback WHERE ts_ms >= ? ORDER BY ts_ms ASC",
            (int(time.time() * 1000) - 7 * 86400 * 1000,)
        )
    rows = cur.fetchall()
    from feedback_hub.db import FEEDBACK_COLUMNS
    return _upsert_mysql(mysql_conn, "feedback", FEEDBACK_COLUMNS, rows)


def syncMessageLabel(sqlite_conn: sqlite3.Connection, mysql_conn: Any, full: bool) -> int:
    """同步 message_label 表。"""
    if full:
        cur = sqlite_conn.execute("SELECT ml.* FROM message_label ml")
    else:
        cur = sqlite_conn.execute(
            "SELECT ml.* FROM message_label ml "
            "WHERE ml.tagged_at >= ?",
            (int(time.time()) - 7 * 86400,)
        )
    rows = cur.fetchall()
    from feedback_hub.db import MESSAGE_LABEL_COLUMNS
    return _upsert_mysql(mysql_conn, "message_label", MESSAGE_LABEL_COLUMNS, rows)


def syncConversationLabel(sqlite_conn: sqlite3.Connection, mysql_conn: Any, full: bool) -> int:
    """同步 conversation_label 表。"""
    if full:
        cur = sqlite_conn.execute("SELECT cl.* FROM conversation_label cl")
    else:
        cur = sqlite_conn.execute(
            "SELECT cl.* FROM conversation_label cl "
            "WHERE cl.aggregated_at >= ?",
            (int(time.time()) - 7 * 86400,)
        )
    rows = cur.fetchall()
    from feedback_hub.db import CONVERSATION_LABEL_COLUMNS
    return _upsert_mysql(mysql_conn, "conversation_label", CONVERSATION_LABEL_COLUMNS, rows)


def syncPushLog(sqlite_conn: sqlite3.Connection, mysql_conn: Any, full: bool) -> int:
    """同步 push_log 表。"""
    if full:
        cur = sqlite_conn.execute("SELECT * FROM push_log ORDER BY id ASC")
    else:
        # 增量：从 MySQL 获取最大 id，只同步比它大的
        with mysql_conn.cursor() as mcur:
            mcur.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM push_log")
            result = mcur.fetchone()
            max_id = result["max_id"] if result else 0
        cur = sqlite_conn.execute(
            "SELECT * FROM push_log WHERE id > ? ORDER BY id ASC",
            (max_id,)
        )
    rows = cur.fetchall()
    from feedback_hub.db import PUSH_LOG_COLUMNS
    return _upsert_mysql(mysql_conn, "push_log", PUSH_LOG_COLUMNS, rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="SQLite → MySQL 增量同步")
    parser.add_argument("--full", action="store_true", help="全量同步（默认增量）")
    parser.add_argument("--sqlite-path", default=None, help="SQLite 文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只统计行数，不写入 MySQL")
    args = parser.parse_args()

    # SQLite 路径
    sqlite_path = args.sqlite_path
    if not sqlite_path:
        from feedback_hub.config import DB_PATH
        sqlite_path = str(DB_PATH)

    if not Path(sqlite_path).exists():
        print(f"[sync] error: SQLite 文件不存在：{sqlite_path}", file=sys.stderr)
        return 1

    print(f"[sync] SQLite: {sqlite_path}")
    print(f"[sync] 模式: {'全量' if args.full else '增量'}")

    # 连接 SQLite
    sqlite_conn = _connect_sqlite(sqlite_path)

    # 统计 SQLite 行数
    for table in ["feedback", "message_label", "conversation_label", "push_log"]:
        cnt = sqlite_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  SQLite {table}: {cnt} 行")

    if args.dry_run:
        sqlite_conn.close()
        return 0

    # 连接 MySQL
    try:
        mysql_conn = _connect_mysql()
        print(f"[sync] MySQL: {_env('MYSQL_HOST')}:{_env('MYSQL_PORT', '3306')}/{_env('MYSQL_DATABASE')}")
    except Exception as e:
        print(f"[sync] error: 无法连接 MySQL: {e}", file=sys.stderr)
        sqlite_conn.close()
        return 1

    # 确保 MySQL schema 已初始化
    try:
        from feedback_hub.db import init_schema
        init_schema(mysql_conn)
        print("[sync] MySQL schema 已就绪")
    except Exception as e:
        print(f"[sync] warn: schema 初始化失败（可能已存在）: {e}")

    # 依次同步
    try:
        n = syncFeedback(sqlite_conn, mysql_conn, args.full)
        print(f"[sync] feedback: 同步 {n} 行")

        n = syncMessageLabel(sqlite_conn, mysql_conn, args.full)
        print(f"[sync] message_label: 同步 {n} 行")

        n = syncConversationLabel(sqlite_conn, mysql_conn, args.full)
        print(f"[sync] conversation_label: 同步 {n} 行")

        n = syncPushLog(sqlite_conn, mysql_conn, args.full)
        print(f"[sync] push_log: 同步 {n} 行")

        print("[sync] 完成 ✓")
    except Exception as e:
        print(f"[sync] error: {e}", file=sys.stderr)
        return 1
    finally:
        sqlite_conn.close()
        mysql_conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
