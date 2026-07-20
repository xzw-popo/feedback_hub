"""测试 puller.py 的纯函数部分（extract_rows + upsert_rows）；网络层由 mock 替换。"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from feedback_hub import db
from feedback_hub import puller


def _sample_resp(ts_ms: int) -> dict:
    return {
        "errCode": 0,
        "results": [{
            "session": {"userVid": "u1", "channel": "wetype", "serviceVid": 10000},
            "msgs": [
                {
                    "sender": 0, "type": "text", "timestamp": ts_ms,
                    "feedbackId": "fb_001",
                    "content": {"text": "闪退", "url": "", "scheme": ""},
                    "clientInfo": {
                        "platform": 1, "appversion": "1.2.3",
                        "device": "iPhone15", "deviceName": "iPhone",
                        "channelid": "x", "enginever": "9", "keyboardSource": "",
                    },
                    "user": {"userVid": "u1"},
                    "tags": ["x", "y"],
                },
                # 客服回复，应被过滤
                {"sender": 1, "type": "text", "timestamp": ts_ms,
                 "feedbackId": "ignore", "content": {"text": "好的"}},
                # 非 text，应被过滤
                {"sender": 0, "type": "image", "timestamp": ts_ms,
                 "feedbackId": "img1", "content": {}},
            ],
        }],
    }


def test_extract_rows_filters_non_user_text():
    ts = 1747526400000
    resp = _sample_resp(ts)
    rows, debug = puller.extract_rows(resp, start_ms=ts - 1000, end_ms=ts + 1000, channel="wetype")
    assert len(rows) == 1
    r = rows[0]
    assert r["feedback_id"] == "fb_001"
    assert r["text"] == "闪退"
    assert r["user_vid"] == "u1"
    assert r["platform"] == "iOS"
    assert r["channel"] == "wetype"
    assert r["msgtype"] == "text"
    assert r["tags"] == "x|y"
    assert debug["user_msg_count"] == 1
    assert debug["in_window_count"] == 1


def test_extract_rows_window_filter():
    ts = 1747526400000
    resp = _sample_resp(ts)
    rows, _ = puller.extract_rows(resp, start_ms=ts + 5000, end_ms=ts + 10000, channel="wetype")
    assert rows == []


def test_extract_rows_skips_msg_without_feedback_id():
    resp = {
        "errCode": 0,
        "results": [{"session": {"userVid": "u1"},
                     "msgs": [{"sender": 0, "type": "text", "timestamp": 1000,
                               "feedbackId": "", "content": {"text": "x"}}]}],
    }
    rows, _ = puller.extract_rows(resp, start_ms=0, end_ms=2000, channel="wetype")
    assert rows == []


def test_extract_rows_raw_json_contains_extra_fields():
    ts = 1747526400000
    resp = _sample_resp(ts)
    rows, _ = puller.extract_rows(resp, start_ms=ts - 1, end_ms=ts + 1, channel="wetype")
    raw = json.loads(rows[0]["raw_json"])
    assert raw["service_channel"] == "wetype"
    assert raw["raw_platform_code"] == 1


def test_extract_rows_builds_original_chat_url():
    ts = 1747526400000
    resp = _sample_resp(ts)
    rows, _ = puller.extract_rows(resp, start_ms=ts - 1, end_ms=ts + 1, channel="wetype")
    assert rows[0]["service_vid"] == 10000
    assert rows[0]["external_chat_url"] == (
        "https://wrfeedback.weread.woa.com/chat?"
        "channel=wetype&serviceVid=10000&userVid=u1"
    )


def test_upsert_rows_returns_inserted_count(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    db.init_schema(conn)
    ts = 1747526400000
    resp = _sample_resp(ts)
    rows, _ = puller.extract_rows(resp, start_ms=ts - 1, end_ms=ts + 1, channel="wetype")
    n1 = puller.upsert_rows(conn, rows)
    n2 = puller.upsert_rows(conn, rows)  # 第二次应全是重复
    assert n1 == 1
    assert n2 == 0
    conn.close()


def test_pull_end_to_end_with_mock(tmp_path, monkeypatch):
    ts_ms = 1747526400000

    def fake_fetch(s_sec, e_sec, **kw):
        return _sample_resp(ts_ms)

    monkeypatch.setattr(puller, "fetch_window", fake_fetch)
    monkeypatch.setattr("feedback_hub.config.DB_PATH", tmp_path / "fb.db")
    monkeypatch.setattr(puller, "RAW_DIR", tmp_path / "raw")

    # 还要让 ensure_dirs 也使用 tmp_path（puller.write_raw_dump 调用 ensure_dirs）
    def fake_ensure_dirs():
        (tmp_path / "raw").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(puller, "ensure_dirs", fake_ensure_dirs)

    start = datetime.fromtimestamp(ts_ms / 1000) - timedelta(seconds=1)
    end = datetime.fromtimestamp(ts_ms / 1000) + timedelta(seconds=1)

    conn = db.connect(tmp_path / "fb.db")
    db.init_schema(conn)
    try:
        result = puller.pull(start, end, conn=conn)
    finally:
        conn.close()

    assert result["fetched_count"] == 1
    assert result["inserted_count"] == 1
    assert result["skipped_dup_count"] == 0
    with db.connect(tmp_path / "fb.db") as verify:
        coverage = verify.execute(
            """SELECT channel, start_ts_ms, end_ts_ms, completed_at_ms
               FROM feedback_source_coverage"""
        ).fetchone()
    assert coverage["channel"] == "wetype"
    assert coverage["start_ts_ms"] == int(start.timestamp()) * 1000
    assert coverage["end_ts_ms"] == int(end.timestamp()) * 1000
    assert coverage["completed_at_ms"] >= coverage["end_ts_ms"]
    assert (tmp_path / "raw").exists()
    raw_files = list((tmp_path / "raw").glob("raw_wetype_*.json"))
    assert len(raw_files) == 1
