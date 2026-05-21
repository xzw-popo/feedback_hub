"""测试 pusher.scorer：评分公式 + bucket 聚合 + Top 5 选取。"""
from __future__ import annotations

import pytest

from feedback_hub.pusher import scorer
from feedback_hub.pusher.config_loader import reset_cache


@pytest.fixture(autouse=True)
def _reset():
    reset_cache()
    yield
    reset_cache()


def _row(conv_id, sev="P0", conf=0.9, ver="5.4.1",
         last_ts_ms=2_000_000_000_000,
         full_text="一直闪退", L2="输入核心",
         top_conf_text="闪退原文", top_conf_fid="f-x"):
    return scorer.ConversationRow(
        conversation_id=conv_id, L1="A.Bug", L2=L2,
        severity=sev, confidence=conf,
        appversion=ver, last_ts_ms=last_ts_ms,
        full_text=full_text, top_conf_text=top_conf_text,
        top_conf_feedback_id=top_conf_fid,
    )


def _weights(d=3.0, p=2.0, c=5.0, r=2.0):
    return {
        "w_dup_count": d, "w_p0_count": p,
        "w_cross_version": c, "w_recent_24h": r,
    }


# ---------- aggregate_and_score ----------

def test_empty_input_returns_empty_list():
    assert scorer.aggregate_and_score([], now_ms=0, weights=_weights()) == []


def test_single_row_score_formula():
    """1 条 P0 + 单版本 + 24h 内 = 3*1 + 2*1 + 5*0 + 2*1 = 7"""
    rows = [_row("c1", sev="P0", last_ts_ms=2_000_000_000_000)]
    out = scorer.aggregate_and_score(
        rows, now_ms=2_000_000_000_000, weights=_weights(),
    )
    assert len(out) == 1
    assert out[0].score == pytest.approx(3 + 2 + 0 + 2)


def test_two_rows_same_signature_aggregate():
    rows = [_row("c1", sev="P0"), _row("c2", sev="P0")]
    out = scorer.aggregate_and_score(
        rows, now_ms=2_000_000_000_000, weights=_weights(),
    )
    assert len(out) == 1
    assert len(out[0].conversations) == 2
    # dup=2, p0=2, cross=0, recent=1 → 6+4+0+2 = 12
    assert out[0].score == pytest.approx(12)


def test_different_major_version_split_into_two_buckets():
    rows = [
        _row("c1", ver="5.4.0"),  # major=5.4
        _row("c2", ver="5.5.0"),  # major=5.5
    ]
    out = scorer.aggregate_and_score(
        rows, now_ms=2_000_000_000_000, weights=_weights(),
    )
    assert len(out) == 2


def test_same_major_diff_minor_one_bucket_with_cross_flag():
    rows = [_row("c1", ver="5.4.0"), _row("c2", ver="5.4.1")]
    out = scorer.aggregate_and_score(
        rows, now_ms=2_000_000_000_000, weights=_weights(),
    )
    assert len(out) == 1
    # dup=2, p0=2, cross=1, recent=1 → 6+4+5+2 = 17
    assert out[0].score == pytest.approx(17)
    assert out[0].cross_version == 1


def test_recent_24h_zero_when_all_old():
    rows = [_row("c1", last_ts_ms=1_000_000_000_000)]
    now_ms = 1_000_000_000_000 + 2 * 86_400_000
    out = scorer.aggregate_and_score(rows, now_ms=now_ms, weights=_weights())
    assert out[0].score == pytest.approx(3 + 2 + 0 + 0)


def test_signature_none_filtered_out():
    rows = [_row("c1", full_text="你好啊很普通的话")]
    out = scorer.aggregate_and_score(
        rows, now_ms=2_000_000_000_000, weights=_weights(),
    )
    assert out == []


def test_representative_picks_highest_confidence():
    rows = [
        _row("low", conf=0.7, top_conf_text="低分文本", top_conf_fid="fl"),
        _row("high", conf=0.95, top_conf_text="高分文本", top_conf_fid="fh"),
    ]
    out = scorer.aggregate_and_score(
        rows, now_ms=2_000_000_000_000, weights=_weights(),
    )
    assert out[0].representative_text == "高分文本"
    assert out[0].representative_conv_id == "high"
    assert out[0].representative_feedback_id == "fh"


def test_sorted_by_score_desc():
    rows = [
        _row("a1", L2="语音", ver="5.0.0", last_ts_ms=1_000_000_000_000),
        _row("b1", L2="输入核心", ver="5.4.0", last_ts_ms=2_000_000_000_000),
        _row("b2", L2="输入核心", ver="5.4.1", last_ts_ms=2_000_000_000_000),
    ]
    out = scorer.aggregate_and_score(
        rows, now_ms=2_000_000_000_000, weights=_weights(),
    )
    assert len(out) == 2
    assert out[0].score >= out[1].score


def test_representative_text_fallback_to_full_text():
    rows = [_row("c1", top_conf_text="", full_text="一直闪退啊" * 30)]
    out = scorer.aggregate_and_score(
        rows, now_ms=2_000_000_000_000, weights=_weights(),
    )
    assert out[0].representative_text == ("一直闪退啊" * 30)[:100]


def test_affected_versions_count():
    rows = [
        _row("c1", ver="5.4.0"), _row("c2", ver="5.4.0"),
        _row("c3", ver="5.4.1"),
    ]
    out = scorer.aggregate_and_score(
        rows, now_ms=2_000_000_000_000, weights=_weights(),
    )
    assert out[0].affected_versions == {"5.4.0": 2, "5.4.1": 1}


# ---------- pick_top5 ----------

def test_pick_top5_with_fewer_than_5():
    rows = [_row(f"c{i}") for i in range(3)]
    groups = scorer.aggregate_and_score(
        rows, now_ms=2_000_000_000_000, weights=_weights(),
    )
    assert len(scorer.pick_top5(groups)) == 1


def test_pick_top5_with_more_than_5_truncates():
    rows = []
    for i, l2 in enumerate(["a", "b", "c", "d", "e", "f"]):
        rows.append(_row(f"c{i}", L2=l2))
    groups = scorer.aggregate_and_score(
        rows, now_ms=2_000_000_000_000, weights=_weights(),
    )
    assert len(groups) == 6
    assert len(scorer.pick_top5(groups)) == 5
