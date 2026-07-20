import hashlib
import sqlite3
from datetime import datetime, timezone

import pytest

from feedback_hub import db
from feedback_hub.topic_mining.contracts import validate_topic_spec
from feedback_hub.topic_mining.source import (
    DataCoverageError,
    UnsupportedSourceFilterError,
    build_item_contexts,
    create_source_snapshot,
    fetch_scoped_items,
)


def _ms(hour, minute=0):
    return int(datetime(2026, 1, 1, hour, minute, tzinfo=timezone.utc).timestamp() * 1000)


@pytest.fixture
def valid_topic_spec():
    return validate_topic_spec(
        {
            "schema_version": 1,
            "topic_name": "工具栏遮挡",
            "objective": "找出全屏时工具栏遮挡的反馈",
            "scope": {
                "start_time": "2026-01-01T00:00:00+00:00",
                "end_time": "2026-01-01T04:00:00+00:00",
                "platforms": ["Win"],
                "channels": ["wetype"],
                "versions": ["3.1"],
            },
            "unit": "feedback",
            "inclusion_criteria": ["工具栏遮挡"],
            "exclusion_criteria": ["无关"],
            "positive_examples": [],
            "negative_examples": [],
            "lexical_hints": {"objects": ["工具栏"], "contexts": ["全屏"]},
            "classification_labels": [
                {"id": "matched", "meaning": "符合"},
                {"id": "not_matched", "meaning": "不符合"},
            ],
            "output": {"preferred_format": "jsonl", "required_fields": ["feedback_text"]},
        }
    )


@pytest.fixture
def source_db(tmp_path):
    path = tmp_path / "formal-feedback.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE feedback (
            feedback_id TEXT PRIMARY KEY, conversation_id TEXT, msg_seq INTEGER,
            ts_ms INTEGER, platform TEXT, appversion TEXT, channel TEXT,
            device_name TEXT, user_vid TEXT, service_vid INTEGER,
            external_chat_url TEXT, text TEXT
        )"""
    )
    rows = [
        ("f0", "c0", 1, _ms(0), "Mac", "3.1", "wetype", "pc", "u0", 77, None, "范围起点"),
        ("f1", "c1", 2, _ms(1), "Win", "3.1", "wetype", "pc", "u1", 77, None, "第一条"),
        ("f2", "c1", 1, _ms(1, 10), "Win", "3.1", "wetype", "pc", "u1", 77, None, "第二条"),
        ("f3", "c2", 1, _ms(1, 25), "Win", "3.1", "wetype", "pc", "u1", 77, None, "第三条"),
        ("f4", "c3", 1, _ms(2), "Mac", "3.1", "wetype", "mac", "u2", 88, None, "错误平台"),
        ("f5", "c4", 1, _ms(2), "Win", "3.1", "other", "pc", "u3", 99, None, "错误渠道"),
        ("f6", "c5", 1, _ms(2), "Win", "3.2", "wetype", "pc", "u4", 99, None, "错误版本"),
        ("f7", "c6", 1, _ms(2), "Win", "3.1", "wetype", "pc", "u5", 99, None, "   "),
        ("f8", "c7", 1, _ms(4), "Win", "3.1", "wetype", "pc", "u6", 99, None, "结束边界"),
    ]
    conn.executemany("INSERT INTO feedback VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.execute(
        """CREATE TABLE feedback_source_coverage (
            channel TEXT NOT NULL, start_ts_ms INTEGER NOT NULL,
            end_ts_ms INTEGER NOT NULL, completed_at_ms INTEGER NOT NULL
        )"""
    )
    conn.execute(
        "INSERT INTO feedback_source_coverage VALUES (?, ?, ?, ?)",
        ("wetype", _ms(0), _ms(4), _ms(5)),
    )
    conn.commit()
    conn.close()
    return path


def test_snapshot_and_scope_do_not_modify_source(tmp_path, source_db, valid_topic_spec):
    before = source_db.read_bytes()
    before_hash = hashlib.sha256(before).hexdigest()

    snapshot = create_source_snapshot(source_db, tmp_path / "snapshot.db")
    items = fetch_scoped_items(snapshot.path, valid_topic_spec)

    assert source_db.read_bytes() == before
    assert hashlib.sha256(source_db.read_bytes()).hexdigest() == before_hash
    assert snapshot.row_count == 9
    assert snapshot.coverage_watermark_ms == _ms(5)
    assert {item["platform"] for item in items} == {"Win"}
    assert [item["feedback_id"] for item in items] == ["f1", "f2", "f3"]


def test_snapshot_can_be_frozen_at_the_run_watermark(tmp_path, source_db):
    cutoff_ms = _ms(2)

    snapshot = create_source_snapshot(
        source_db, tmp_path / "bounded-snapshot.db",
        max_ts_ms=cutoff_ms,
    )

    assert snapshot.max_ts_ms == cutoff_ms
    assert snapshot.row_count == 8
    with sqlite3.connect(snapshot.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM feedback WHERE ts_ms > ?", (cutoff_ms,),
        ).fetchone()[0] == 0


def test_feedback_scope_uses_half_open_time_bounds_and_stable_item_keys(source_db, valid_topic_spec):
    items = fetch_scoped_items(source_db, valid_topic_spec)

    assert {frozenset(item) for item in items} == {frozenset({
        "item_id", "feedback_id", "conversation_id", "ts_ms", "platform", "appversion",
        "channel", "device_name", "user_vid", "service_vid", "text",
        "source_url",
    })}
    assert [item["item_id"] for item in items] == ["f1", "f2", "f3"]
    assert len({item["feedback_id"] for item in items}) == len(items)
    assert all(item["ts_ms"] < _ms(4) for item in items)


def test_conversation_items_aggregate_in_msg_sequence_order_and_fallback_source_url(source_db, valid_topic_spec):
    conversation_spec = validate_topic_spec({**valid_topic_spec.to_dict(), "unit": "conversation"})

    items = fetch_scoped_items(source_db, conversation_spec)

    c1 = next(item for item in items if item["item_id"] == "c1")
    assert c1["feedback_id"] is None
    assert c1["text"] == "第二条\n第一条"
    assert c1["source_url"] == db.build_external_chat_url("wetype", 77, "u1")


def test_build_feedback_context_prefers_ready_conversation_identity(
    source_db, valid_topic_spec,
):
    scoped_feedback_items = fetch_scoped_items(source_db, valid_topic_spec)

    contexts = build_item_contexts(scoped_feedback_items, unit="feedback", window_ms=1_800_000)

    assert [row["feedback_id"] for row in contexts["f2"]] == ["f1", "f2"]


def test_blank_user_ids_never_share_context_between_unrelated_feedback():
    items = [
        {
            "item_id": "anonymous-a", "feedback_id": "anonymous-a",
            "conversation_id": "pending", "user_vid": "", "ts_ms": 1,
            "text": "工具栏遮挡", "channel": "wetype",
        },
        {
            "item_id": "anonymous-b", "feedback_id": "anonymous-b",
            "conversation_id": "pending", "user_vid": "", "ts_ms": 2,
            "text": "无关隐私文本", "channel": "wetype",
        },
    ]

    contexts = build_item_contexts(items, unit="feedback", window_ms=1_800_000)

    assert [row["item_id"] for row in contexts["anonymous-a"]] == [
        "anonymous-a",
    ]
    assert [row["item_id"] for row in contexts["anonymous-b"]] == [
        "anonymous-b",
    ]


def test_conversation_unit_rejects_placeholder_conversation_ids(
    source_db, valid_topic_spec,
):
    with sqlite3.connect(source_db) as connection:
        connection.executemany(
            "INSERT INTO feedback VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "pending-a", "pending", 1, _ms(2, 10), "Win", "3.1",
                    "wetype", "pc", "user-a", 77, None, "用户 A",
                ),
                (
                    "pending-b", "pending", 1, _ms(2, 20), "Win", "3.1",
                    "wetype", "pc", "user-b", 77, None, "用户 B",
                ),
            ],
        )
    conversation_spec = validate_topic_spec({
        **valid_topic_spec.to_dict(), "unit": "conversation",
    })

    with pytest.raises(ValueError, match="conversation_source_not_ready"):
        fetch_scoped_items(source_db, conversation_spec)


def test_explicit_pull_coverage_allows_a_quiet_interval_tail(
    source_db, valid_topic_spec,
):
    with sqlite3.connect(source_db) as connection:
        connection.execute("DELETE FROM feedback WHERE ts_ms = ?", (_ms(4),))
        connection.execute("DELETE FROM feedback_source_coverage")
        connection.execute(
            "INSERT INTO feedback_source_coverage VALUES (?, ?, ?, ?)",
            ("wetype", _ms(0), _ms(4), _ms(5)),
        )

    items = fetch_scoped_items(source_db, valid_topic_spec)

    assert [item["feedback_id"] for item in items] == ["f1", "f2", "f3"]


def test_explicit_pull_coverage_rejects_an_internal_gap(
    source_db, valid_topic_spec,
):
    with sqlite3.connect(source_db) as connection:
        connection.execute("DELETE FROM feedback_source_coverage")
        connection.executemany(
            "INSERT INTO feedback_source_coverage VALUES (?, ?, ?, ?)",
            [
                ("wetype", _ms(0), _ms(2), _ms(5)),
                ("wetype", _ms(3), _ms(4), _ms(5, 1)),
            ],
        )

    with pytest.raises(DataCoverageError):
        fetch_scoped_items(source_db, valid_topic_spec)


def test_missing_pull_coverage_metadata_is_a_stable_coverage_error(
    source_db, valid_topic_spec,
):
    with sqlite3.connect(source_db) as connection:
        connection.execute("DROP TABLE feedback_source_coverage")

    with pytest.raises(DataCoverageError):
        fetch_scoped_items(source_db, valid_topic_spec)


def test_missing_source_date_range_raises_before_retrieval(source_db, valid_topic_spec):
    uncovered = validate_topic_spec({
        **valid_topic_spec.to_dict(),
        "scope": {**valid_topic_spec.to_dict()["scope"], "start_time": "2025-12-31T00:00:00+00:00"},
    })

    with pytest.raises(DataCoverageError) as error:
        fetch_scoped_items(source_db, uncovered)

    assert error.value.start_ms < error.value.source_min_ms


def test_non_wetype_product_scope_is_rejected_before_source_retrieval(tmp_path, valid_topic_spec):
    unsupported = validate_topic_spec({
        **valid_topic_spec.to_dict(),
        "scope": {**valid_topic_spec.to_dict()["scope"], "products": ["另一款产品"]},
    })

    with pytest.raises(UnsupportedSourceFilterError, match="微信输入法"):
        fetch_scoped_items(tmp_path / "not-opened.db", unsupported)
