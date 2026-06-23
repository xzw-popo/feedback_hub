from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from feedback_hub.config import DB_PATH
from feedback_hub.weibo.llm_labeler import WeiboLLMLabelError, classify_post_with_llm
from feedback_hub.weibo.store import apply_label, init_schema, iter_posts_for_labeling


def run_backfill(
    conn: Any,
    *,
    classifier: Callable[[dict[str, Any]], dict[str, Any]] = classify_post_with_llm,
    limit: int = 50,
    only_rule: bool = True,
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
) -> dict[str, int]:
    result = {"seen": 0, "labeled": 0, "hidden": 0, "failed": 0}
    for post in iter_posts_for_labeling(conn, limit=limit, only_rule=only_rule):
        result["seen"] += 1
        try:
            label = classifier(post)
        except Exception as exc:
            result["failed"] += 1
            if progress:
                progress(f"[{result['seen']}/{limit}] failed {post['weibo_id']}: {exc}")
            continue
        if dry_run:
            if label.get("is_relevant") is False:
                result["hidden"] += 1
            else:
                result["labeled"] += 1
            if progress:
                progress(f"[{result['seen']}/{limit}] dry-run {post['weibo_id']} relevant={label.get('is_relevant')}")
            continue
        apply_label(conn, post["weibo_id"], label, labeled_at=int(time.time()))
        conn.commit()
        if label.get("is_relevant") is False:
            result["hidden"] += 1
            status = "hidden"
        else:
            result["labeled"] += 1
            status = "labeled"
        if progress:
            progress(
                f"[{result['seen']}/{limit}] {status} {post['weibo_id']} "
                f"{label.get('brand_focus')}/{label.get('sentiment')}/{label.get('risk_level')}"
            )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill Weibo labels with the configured LLM.")
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite database path")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--all", action="store_true", help="Relabel posts even when label_source is already llm")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    conn = sqlite3.connect(Path(args.db))
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    try:
        progress = None if args.quiet else print
        result = run_backfill(
            conn,
            limit=args.limit,
            only_rule=not args.all,
            dry_run=args.dry_run,
            progress=progress,
        )
    finally:
        conn.close()
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
