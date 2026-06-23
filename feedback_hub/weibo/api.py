from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from feedback_hub import db
from feedback_hub.weibo import store


router = APIRouter(prefix="/api/weibo", tags=["weibo"])


def _conn(db_path: str | None = None):
    conn = db.connect(db_path) if db_path else db.connect()
    store.init_schema(conn)
    return conn


def make_router(db_path: str | None = None) -> APIRouter:
    api = APIRouter(prefix="/api/weibo", tags=["weibo"])

    @api.get("/stats")
    def stats(
        from_: Optional[str] = Query(None, alias="from"),
        to: Optional[str] = None,
    ):
        with _conn(db_path) as conn:
            return store.get_stats(conn, from_=from_, to=to)

    @api.get("/posts")
    def posts(
        from_: Optional[str] = Query(None, alias="from"),
        to: Optional[str] = None,
        q: Optional[str] = None,
        brand_focus: Optional[str] = None,
        sentiment: Optional[str] = None,
        topic: Optional[str] = None,
        post_type: Optional[str] = None,
        risk_level: Optional[str] = None,
        keyword: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ):
        if not (1 <= limit <= 500):
            raise HTTPException(status_code=400, detail="limit 必须在 [1, 500]")
        if offset < 0:
            raise HTTPException(status_code=400, detail="offset 不能为负")
        with _conn(db_path) as conn:
            return store.list_posts(
                conn,
                from_=from_,
                to=to,
                q=q,
                brand_focus=brand_focus,
                sentiment=sentiment,
                topic=topic,
                post_type=post_type,
                risk_level=risk_level,
                keyword=keyword,
                limit=limit,
                offset=offset,
            )

    @api.get("/posts/{post_id}")
    def post_detail(post_id: str):
        with _conn(db_path) as conn:
            post = store.get_post(conn, post_id)
            if post is None:
                raise HTTPException(status_code=404, detail="weibo post not found")
            return post

    @api.get("/crawl-runs")
    def crawl_runs(limit: int = 20):
        if not (1 <= limit <= 100):
            raise HTTPException(status_code=400, detail="limit 必须在 [1, 100]")
        with _conn(db_path) as conn:
            return store.list_crawl_runs(conn, limit=limit)

    return api
