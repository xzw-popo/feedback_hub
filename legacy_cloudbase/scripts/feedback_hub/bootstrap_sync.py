"""一次性数据同步脚本：SQLite → MySQL。

在 CloudRun 容器启动时由 entrypoint.sh 调用，
将内置的 SQLite 数据导入到 MySQL（仅当 MySQL 为空时执行）。

此脚本不依赖 sync_to_cloud.py，避免模块级名称问题。
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path


def main() -> int:
    # SQLite 路径
    sqlite_path = os.environ.get("SQLITE_PATH", "/app/feedback_hub/data/feedback.db")
    if not Path(sqlite_path).exists():
        print(f"[bootstrap] SQLite not found: {sqlite_path}, skip sync")
        return 0

    import pymysql
    from feedback_hub.db import (
        CONVERSATION_LABEL_COLUMNS,
        FEEDBACK_COLUMNS,
        MESSAGE_LABEL_COLUMNS,
        PUSH_LOG_COLUMNS,
    )

    # 连接
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row

    mysql_conn = pymysql.connect(
        host=os.environ.get("MYSQL_HOST", "localhost"),
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ.get("MYSQL_USER", "root"),
        password=os.environ.get("MYSQL_PASSWORD", ""),
        database=os.environ.get("MYSQL_DATABASE", "feedback_hub"),
        charset="utf8mb4",
        autocommit=False,
    )

    def upsert_batch(table: str, columns: tuple[str, ...], batch_size: int = 500) -> int:
        cols_str = ", ".join(f"`{c}`" for c in columns)
        ph = ", ".join(["%s"] * len(columns))
        update_str = ", ".join(f"`{c}`=VALUES(`{c}`)" for c in columns)
        sql = f"INSERT INTO `{table}` ({cols_str}) VALUES ({ph}) ON DUPLICATE KEY UPDATE {update_str}"

        rows = sqlite_conn.execute(f"SELECT * FROM {table}").fetchall()
        total = len(rows)
        count = 0
        for i in range(0, total, batch_size):
            batch = rows[i:i + batch_size]
            vals = [tuple(r[c] for c in columns) for r in batch]
            with mysql_conn.cursor() as cur:
                cur.executemany(sql, vals)
            mysql_conn.commit()
            count += len(batch)
            print(f"  {table}: {count}/{total}")
        return count

    try:
        # conversation_label 最关键（API 依赖）
        n = upsert_batch("conversation_label", CONVERSATION_LABEL_COLUMNS)
        print(f"[bootstrap] conversation_label: {n} rows synced")

        n = upsert_batch("feedback", FEEDBACK_COLUMNS)
        print(f"[bootstrap] feedback: {n} rows synced")

        n = upsert_batch("message_label", MESSAGE_LABEL_COLUMNS)
        print(f"[bootstrap] message_label: {n} rows synced")

        n = upsert_batch("push_log", PUSH_LOG_COLUMNS)
        print(f"[bootstrap] push_log: {n} rows synced")

        print("[bootstrap] Sync complete!")
    except Exception as e:
        print(f"[bootstrap] error: {e}", file=sys.stderr)
        return 1
    finally:
        sqlite_conn.close()
        mysql_conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
