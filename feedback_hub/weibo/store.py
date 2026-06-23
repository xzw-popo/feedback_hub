from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from feedback_hub.weibo.labels import classify_post, is_relevant_post


SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def init_schema(conn: Any) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _ph(n: int) -> str:
    return ", ".join(["?"] * n)


def upsert_post(
    conn: Any,
    row: dict[str, Any],
    *,
    keyword: str,
    query_name: str | None = None,
    searched_at: int | None = None,
    search_rank: int = 1,
    source_mode: str = "pc",
) -> dict[str, Any]:
    now = int(time.time()) if searched_at is None else searched_at
    weibo_id = str(row.get("weibo_id") or row.get("id") or "")
    if not weibo_id:
        raise ValueError("weibo_id is required")
    author = row.get("author") or {}
    text = str(row.get("text") or "")
    if not is_relevant_post(text, keyword):
        return {"inserted": False, "weibo_id": weibo_id, "skipped": True}
    existing = conn.execute("SELECT weibo_id FROM weibo_post WHERE weibo_id = ?", (weibo_id,)).fetchone()
    post_id = str(row.get("id") or f"wb_{weibo_id}")
    payload = (
        post_id,
        weibo_id,
        row.get("source") or "weibo",
        row.get("url") or f"https://weibo.com/{weibo_id}",
        str(author.get("user_id") or row.get("author_id") or ""),
        str(author.get("screen_name") or row.get("author_name") or ""),
        1 if bool(author.get("verified") or row.get("author_verified")) else 0,
        row.get("created_at_raw") or "",
        row.get("created_at_ms"),
        text,
        _dumps(row.get("pic_urls") or []),
        row.get("reposts_count"),
        row.get("comments_count"),
        row.get("attitudes_count"),
        now,
        now,
        _dumps(row),
    )
    if existing is None:
        conn.execute(
            """
            INSERT INTO weibo_post (
                id, weibo_id, source, url, author_id, author_name, author_verified,
                created_at_raw, created_at_ms, text, pic_urls_json, reposts_count,
                comments_count, attitudes_count, first_seen_at, last_seen_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        inserted = True
    else:
        conn.execute(
            """
            UPDATE weibo_post
            SET source = ?, url = ?, author_id = ?, author_name = ?, author_verified = ?,
                created_at_raw = ?, created_at_ms = ?, text = ?, pic_urls_json = ?,
                reposts_count = ?, comments_count = ?, attitudes_count = ?,
                last_seen_at = ?, raw_json = ?
            WHERE weibo_id = ?
            """,
            payload[2:14] + (now, _dumps(row), weibo_id),
        )
        inserted = False
    conn.execute(
        """
        INSERT INTO weibo_hit (weibo_id, keyword, query_name, searched_at, search_rank, source_mode)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (weibo_id, keyword, query_name or keyword, now, search_rank, source_mode),
    )
    label = classify_post(text)
    conn.execute(
        """
        INSERT OR REPLACE INTO weibo_label (
            weibo_id, brand_focus, sentiment, topics_json, post_type, risk_level,
            confidence, reason, label_source, labeled_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            weibo_id,
            label["brand_focus"],
            label["sentiment"],
            _dumps(label["topics"]),
            label["post_type"],
            label["risk_level"],
            label["confidence"],
            label["reason"],
            label["label_source"],
            now,
        ),
    )
    conn.commit()
    return {"inserted": inserted, "weibo_id": weibo_id}


def _build_filters(
    *,
    from_: str | None = None,
    to: str | None = None,
    q: str | None = None,
    brand_focus: str | None = None,
    sentiment: str | None = None,
    topic: str | None = None,
    post_type: str | None = None,
    risk_level: str | None = None,
    keyword: str | None = None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    event_time_expr = "COALESCE(wp.created_at_ms, wp.last_seen_at * 1000)"
    if from_:
        clauses.append(f"{event_time_expr} >= ?")
        params.append(_parse_date_ms(from_, end_of_day=False))
    if to:
        clauses.append(f"{event_time_expr} <= ?")
        params.append(_parse_date_ms(to, end_of_day=True))
    if q:
        clauses.append("wp.text LIKE ?")
        params.append(f"%{q}%")
    if brand_focus:
        clauses.append("wl.brand_focus = ?")
        params.append(brand_focus)
    if sentiment:
        clauses.append("wl.sentiment = ?")
        params.append(sentiment)
    if topic:
        clauses.append("wl.topics_json LIKE ?")
        params.append(f"%{topic}%")
    if post_type:
        clauses.append("wl.post_type = ?")
        params.append(post_type)
    if risk_level:
        clauses.append("wl.risk_level = ?")
        params.append(risk_level)
    if keyword:
        clauses.append("wp.weibo_id IN (SELECT weibo_id FROM weibo_hit WHERE keyword = ? OR query_name = ?)")
        params.extend([keyword, keyword])
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


def _parse_date_ms(value: str, *, end_of_day: bool) -> int:
    from datetime import datetime

    value = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value, fmt)
            if fmt == "%Y-%m-%d" and end_of_day:
                dt = dt.replace(hour=23, minute=59, second=59)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    raise ValueError(f"invalid date: {value}")


def _row_to_item(row: Any, keywords: list[str]) -> dict[str, Any]:
    return {
        "id": _row_get(row, "id"),
        "weibo_id": _row_get(row, "weibo_id"),
        "url": _row_get(row, "url"),
        "author_name": _row_get(row, "author_name") or "",
        "author_id": _row_get(row, "author_id") or "",
        "author_verified": bool(_row_get(row, "author_verified")),
        "created_at_raw": _row_get(row, "created_at_raw") or "",
        "created_at_ms": _row_get(row, "created_at_ms"),
        "text": _row_get(row, "text") or "",
        "pic_urls": _loads(_row_get(row, "pic_urls_json"), []),
        "reposts_count": _row_get(row, "reposts_count"),
        "comments_count": _row_get(row, "comments_count"),
        "attitudes_count": _row_get(row, "attitudes_count"),
        "keywords": keywords,
        "brand_focus": _row_get(row, "brand_focus"),
        "sentiment": _row_get(row, "sentiment"),
        "topics": _loads(_row_get(row, "topics_json"), []),
        "post_type": _row_get(row, "post_type"),
        "risk_level": _row_get(row, "risk_level"),
        "confidence": _row_get(row, "confidence"),
        "reason": _row_get(row, "reason"),
        "label_source": _row_get(row, "label_source"),
        "first_seen_at": _row_get(row, "first_seen_at"),
        "last_seen_at": _row_get(row, "last_seen_at"),
    }


def _keyword_map(conn: Any, weibo_ids: list[str]) -> dict[str, list[str]]:
    if not weibo_ids:
        return {}
    rows = conn.execute(
        f"SELECT weibo_id, query_name FROM weibo_hit WHERE weibo_id IN ({_ph(len(weibo_ids))}) ORDER BY searched_at ASC",
        weibo_ids,
    ).fetchall()
    out: dict[str, list[str]] = {}
    for row in rows:
        wid = str(_row_get(row, "weibo_id"))
        name = str(_row_get(row, "query_name") or "")
        if name and name not in out.setdefault(wid, []):
            out[wid].append(name)
    return out


def list_posts(
    conn: Any,
    *,
    from_: str | None = None,
    to: str | None = None,
    q: str | None = None,
    brand_focus: str | None = None,
    sentiment: str | None = None,
    topic: str | None = None,
    post_type: str | None = None,
    risk_level: str | None = None,
    keyword: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    where, params = _build_filters(
        from_=from_,
        to=to,
        q=q,
        brand_focus=brand_focus,
        sentiment=sentiment,
        topic=topic,
        post_type=post_type,
        risk_level=risk_level,
        keyword=keyword,
    )
    total = conn.execute(
        f"SELECT COUNT(*) AS cnt FROM weibo_post wp JOIN weibo_label wl ON wp.weibo_id = wl.weibo_id {where}",
        params,
    ).fetchone()["cnt"]
    rows = conn.execute(
        f"""
        SELECT wp.*, wl.brand_focus, wl.sentiment, wl.topics_json, wl.post_type,
               wl.risk_level, wl.confidence, wl.reason, wl.label_source
        FROM weibo_post wp
        JOIN weibo_label wl ON wp.weibo_id = wl.weibo_id
        {where}
        ORDER BY COALESCE(wp.created_at_ms, wp.last_seen_at * 1000) DESC, wp.weibo_id DESC
        LIMIT ? OFFSET ?
        """,
        params + [limit, offset],
    ).fetchall()
    ids = [str(_row_get(row, "weibo_id")) for row in rows]
    keywords = _keyword_map(conn, ids)
    return {
        "total": total,
        "items": [_row_to_item(row, keywords.get(str(_row_get(row, "weibo_id")), [])) for row in rows],
    }


def get_post(conn: Any, post_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT wp.*, wl.brand_focus, wl.sentiment, wl.topics_json, wl.post_type,
               wl.risk_level, wl.confidence, wl.reason, wl.label_source
        FROM weibo_post wp
        JOIN weibo_label wl ON wp.weibo_id = wl.weibo_id
        WHERE wp.id = ? OR wp.weibo_id = ?
        """,
        (post_id, post_id),
    ).fetchone()
    if row is None:
        return None
    keywords = _keyword_map(conn, [str(_row_get(row, "weibo_id"))])
    item = _row_to_item(row, keywords.get(str(_row_get(row, "weibo_id")), []))
    item["raw"] = _loads(_row_get(row, "raw_json"), {})
    return item


def get_stats(conn: Any, *, from_: str | None = None, to: str | None = None) -> dict[str, Any]:
    where, params = _build_filters(from_=from_, to=to)
    rows = conn.execute(
        f"""
        SELECT wl.brand_focus, wl.sentiment, wl.topics_json, wl.risk_level,
               wp.created_at_ms, wp.last_seen_at
        FROM weibo_post wp
        JOIN weibo_label wl ON wp.weibo_id = wl.weibo_id
        {where}
        """,
        params,
    ).fetchall()
    brand: dict[str, int] = {}
    sentiment: dict[str, int] = {}
    topic: dict[str, int] = {}
    risk: dict[str, int] = {}
    trend: dict[str, dict[str, int]] = {}
    for row in rows:
        brand_focus = str(_row_get(row, "brand_focus"))
        brand[brand_focus] = brand.get(brand_focus, 0) + 1
        sent = str(_row_get(row, "sentiment"))
        sentiment[sent] = sentiment.get(sent, 0) + 1
        level = str(_row_get(row, "risk_level"))
        risk[level] = risk.get(level, 0) + 1
        for t in _loads(_row_get(row, "topics_json"), []):
            topic[t] = topic.get(t, 0) + 1
        event_ms = _row_get(row, "created_at_ms")
        if event_ms is None:
            event_ms = int(_row_get(row, "last_seen_at") or 0) * 1000
        if event_ms:
            bucket = time.strftime("%Y-%m-%d", time.localtime(int(event_ms) / 1000))
            trend.setdefault(bucket, {"total": 0, "wechat": 0, "doubao": 0, "comparison": 0})
            trend[bucket]["total"] += 1
            if brand_focus in ("wechat", "doubao", "comparison"):
                trend[bucket][brand_focus] += 1
    high_risk = list_posts(conn, risk_level="high", limit=10, offset=0)["items"]
    latest_run = conn.execute(
        "SELECT * FROM weibo_crawl_run ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    return {
        "total_posts": len(rows),
        "brand_focus_counts": brand,
        "sentiment_counts": sentiment,
        "topic_counts": topic,
        "risk_counts": risk,
        "trend": [{"bucket": k, "counts": v} for k, v in sorted(trend.items())],
        "latest_crawl_run": dict(latest_run) if latest_run is not None else None,
        "high_risk_posts": high_risk,
    }


def list_crawl_runs(conn: Any, *, limit: int = 20) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT * FROM weibo_crawl_run ORDER BY started_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return {"items": [dict(row) for row in rows]}


def create_crawl_run(conn: Any, *, status: str, config: dict[str, Any], started_at: int | None = None) -> str:
    run_id = f"wcr_{uuid4().hex[:12]}"
    conn.execute(
        """
        INSERT INTO weibo_crawl_run (id, status, config_json, started_at)
        VALUES (?, ?, ?, ?)
        """,
        (run_id, status, _dumps(config), started_at or int(time.time())),
    )
    conn.commit()
    return run_id
