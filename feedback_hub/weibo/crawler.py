from __future__ import annotations

import json
import re
import time
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PC_SEARCH_URL = "https://s.weibo.com/weibo"


def strip_html(text: str) -> str:
    text = text.replace("<br />", "\n").replace("<br/>", "\n")
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(text).strip()


def parse_time_scope_part(value: str) -> str:
    value = value.strip()
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{1,2}))?", value)
    if not match:
        raise ValueError(f"invalid time value: {value!r}; expected YYYY-MM-DD or YYYY-MM-DD HH")
    year, month, day, hour = match.groups()
    return f"{year}-{month}-{day}-{int(hour or 0)}"


def build_timescope(start: str | None, end: str | None) -> str | None:
    if not start and not end:
        return None
    if not start or not end:
        raise ValueError("start and end must be provided together")
    return f"custom:{parse_time_scope_part(start)}:{parse_time_scope_part(end)}"


def build_pc_search_url(
    keyword: str,
    page: int,
    *,
    start: str | None = None,
    end: str | None = None,
    sort: str = "default",
) -> str:
    params: dict[str, Any] = {"q": keyword, "page": page}
    timescope = build_timescope(start, end)
    if timescope:
        params["timescope"] = timescope
    if sort == "time":
        params["xsort"] = "time"
    return f"{PC_SEARCH_URL}?{urlencode(params)}"


def base_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }


def fetch_pc_search_html(
    keyword: str,
    page: int,
    cookie: str,
    timeout: int = 20,
    *,
    start: str | None = None,
    end: str | None = None,
    sort: str = "default",
) -> str:
    headers = base_headers() | {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://s.weibo.com/",
    }
    if cookie:
        headers["Cookie"] = cookie
    req = Request(build_pc_search_url(keyword, page, start=start, end=end, sort=sort), headers=headers)
    with urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def first_match(pattern: str, text: str, flags: int = re.S) -> str:
    match = re.search(pattern, text, flags)
    return unescape(match.group(1)).strip() if match else ""


def extract_pc_from_field(block: str) -> tuple[str, str]:
    time_link = re.search(
        r'<div class="from"[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*(?:click:wb_time|wb_time)[^>]*>(.*?)</a>',
        block,
        re.S,
    )
    if not time_link:
        time_link = re.search(r'<div class="from"[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
    if not time_link:
        return "", ""
    url = unescape(time_link.group(1)).strip()
    if url.startswith("//"):
        url = "https:" + url
    return url, strip_html(unescape(time_link.group(2)).strip())


def parse_pc_cards(html: str, keyword: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    blocks = re.findall(
        r'<!--card-wrap-->\s*(<div class="card-wrap" action-type="feed_list_item".*?)<!--/card-wrap-->',
        html,
        re.S,
    )
    for block in blocks:
        mid = first_match(r'\bmid="(\d+)"', block, 0)
        if not mid:
            continue
        uid = first_match(r'href="//weibo\.com/(\d+)\?refer_flag=', block, 0)
        screen_name = first_match(r'class="name"[^>]*nick-name="([^"]+)"', block, 0)
        if not screen_name:
            screen_name = strip_html(first_match(r'<a[^>]+class="name"[^>]*>(.*?)</a>', block))
        url = first_match(r"copyurl\('([^']+)'\)", block, 0)
        from_url, created_at = extract_pc_from_field(block)
        if not url:
            url = from_url
        full_text = first_match(r'<p class="txt" node-type="feed_list_content_full"[^>]*>(.*?)</p>', block)
        short_text = first_match(r'<p class="txt" node-type="feed_list_content"[^>]*>(.*?)</p>', block)
        text = strip_html(full_text or short_text)
        pic_ids = []
        pic_blob = first_match(r'pic_ids=([^"&]+)', block, 0)
        if pic_blob:
            pic_ids = [p for p in pic_blob.split(",") if p]
        rows.append(
            {
                "source": "weibo_pc",
                "keyword": keyword,
                "weibo_id": mid,
                "bid": "",
                "url": url or f"https://weibo.com/{uid}/{mid}",
                "created_at_raw": created_at,
                "author": {
                    "user_id": uid,
                    "screen_name": screen_name,
                    "verified": False,
                },
                "text": text,
                "pic_ids": pic_ids,
                "pic_urls": [f"https://wx1.sinaimg.cn/large/{pid}.jpg" for pid in pic_ids],
                "raw": {"html": block},
            }
        )
    return rows


def crawl_pc(
    keyword: str,
    pages: int,
    cookie: str,
    *,
    start: str | None = None,
    end: str | None = None,
    sort: str = "default",
    delay_seconds: float = 0.5,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, pages + 1):
        html = fetch_pc_search_html(keyword, page, cookie, start=start, end=end, sort=sort)
        for row in parse_pc_cards(html, keyword):
            if row["weibo_id"] not in seen:
                rows.append(row)
                seen.add(row["weibo_id"])
        time.sleep(delay_seconds)
    return rows


def load_query_config(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("queries") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("query config must be a list or an object with a queries list")
    queries: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, str):
            keyword = item.strip()
            if keyword:
                queries.append({"name": keyword, "keyword": keyword})
            continue
        if isinstance(item, dict):
            keyword = str(item.get("keyword") or "").strip()
            if not keyword:
                raise ValueError(f"query item missing keyword: {item!r}")
            query = dict(item)
            query["keyword"] = keyword
            query["name"] = str(query.get("name") or keyword)
            queries.append(query)
            continue
        raise ValueError(f"unsupported query item: {item!r}")
    return queries
