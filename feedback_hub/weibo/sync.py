from __future__ import annotations

import argparse
import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from feedback_hub.config import DB_PATH
from feedback_hub.weibo.crawler import crawl_pc, load_query_config
from feedback_hub.weibo.label_backfill import run_backfill
from feedback_hub.weibo.llm_labeler import classify_post_with_llm
from feedback_hub.weibo.store import create_crawl_run, finish_crawl_run, init_schema, upsert_post


DEFAULT_QUERIES = [
    {"name": "wx_input", "keyword": "微信输入法", "pages": 5},
    {"name": "wx_keyboard", "keyword": "微信键盘", "pages": 5},
    {"name": "doubao_input", "keyword": "豆包输入法", "pages": 5},
    {"name": "compare_wx_doubao", "keyword": "微信输入法 豆包", "pages": 5},
    {"name": "compare_keyboard_doubao", "keyword": "微信键盘 豆包", "pages": 5},
]


def _load_queries(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return [dict(q) for q in DEFAULT_QUERIES]
    return load_query_config(path)


def _crawl_with_test_module(
    *,
    keyword: str,
    pages: int,
    cookie: str,
    start: str,
    end: str,
    sort: str,
) -> list[dict[str, Any]]:
    return crawl_pc(keyword, pages, cookie, start=start, end=end, sort=sort)


def _window_from_last(last: str, *, now: datetime | None = None) -> tuple[str, str]:
    base = now or datetime.now()
    value = last.strip().lower()
    if value.endswith("h"):
        delta = timedelta(hours=int(value[:-1]))
    elif value.endswith("d"):
        delta = timedelta(days=int(value[:-1]))
    elif value.endswith("m"):
        delta = timedelta(minutes=int(value[:-1]))
    else:
        raise ValueError("--last must end with m, h, or d")
    start = base - delta
    return start.strftime("%Y-%m-%d %H"), base.strftime("%Y-%m-%d %H")


def run_sync(
    conn: Any,
    *,
    queries: list[dict[str, Any]],
    cookie: str,
    start: str,
    end: str,
    sort: str = "time",
    source_mode: str = "pc",
    crawler: Callable[..., list[dict[str, Any]]] = _crawl_with_test_module,
    labeler: Callable[[dict[str, Any]], dict[str, Any]] = classify_post_with_llm,
    label_limit: int = 500,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if not cookie.strip():
        raise ValueError("WEIBO_COOKIE is required")
    init_schema(conn)
    config = {"queries": queries, "start": start, "end": end, "sort": sort, "source_mode": source_mode}
    run_id = create_crawl_run(conn, status="running", config=config)
    total_seen = 0
    inserted_posts = 0
    try:
        for query in queries:
            keyword = str(query["keyword"])
            pages = int(query.get("pages") or 1)
            name = str(query.get("name") or keyword)
            rows = crawler(keyword=keyword, pages=pages, cookie=cookie, start=start, end=end, sort=sort)
            total_seen += len(rows)
            if progress:
                progress(f"[crawl] {name} keyword={keyword} rows={len(rows)}")
            for idx, row in enumerate(rows, 1):
                result = upsert_post(
                    conn,
                    row,
                    keyword=keyword,
                    query_name=name,
                    searched_at=int(time.time()),
                    search_rank=idx,
                    source_mode=source_mode,
                    skip_irrelevant=False,
                )
                if result.get("inserted"):
                    inserted_posts += 1
        label_result = run_backfill(conn, classifier=labeler, limit=label_limit, only_rule=True, progress=progress)
        finish_crawl_run(
            conn,
            run_id,
            status="succeeded",
            total_seen=total_seen,
            inserted_posts=inserted_posts,
        )
        return {
            "run_id": run_id,
            "status": "succeeded",
            "total_seen": total_seen,
            "inserted_posts": inserted_posts,
            "label_result": label_result,
        }
    except Exception as exc:
        finish_crawl_run(
            conn,
            run_id,
            status="failed",
            total_seen=total_seen,
            inserted_posts=inserted_posts,
            error_message=str(exc),
        )
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Crawl Weibo posts, ingest them, and run LLM labels.")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--cookie", default=os.environ.get("WEIBO_COOKIE", ""))
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--last", help="Rolling window such as 2h, 24h, or 1d")
    parser.add_argument("--sort", choices=("default", "time"), default="time")
    parser.add_argument("--label-limit", type=int, default=500)
    args = parser.parse_args(argv)
    if args.last:
        start, end = _window_from_last(args.last)
    else:
        if not args.start or not args.end:
            raise SystemExit("provide --last or both --start and --end")
        start, end = args.start, args.end
    conn = sqlite3.connect(Path(args.db))
    conn.row_factory = sqlite3.Row
    try:
        result = run_sync(
            conn,
            queries=_load_queries(args.config),
            cookie=args.cookie,
            start=start,
            end=end,
            sort=args.sort,
            label_limit=args.label_limit,
            progress=print,
        )
    finally:
        conn.close()
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
