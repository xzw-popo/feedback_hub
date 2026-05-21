"""测试 pusher.formatter：候选 → Markdown 字符串（spec 阶段 2 §4.1）。"""
from __future__ import annotations

import pytest

from feedback_hub.pusher import formatter, scorer
from feedback_hub.pusher.config_loader import reset_cache


@pytest.fixture(autouse=True)
def _reset():
    reset_cache()
    yield
    reset_cache()


def _group(sig=("crash", "输入核心", "5.4"), *, dup=12, p0=8,
           cross=0, versions=None, rep_text="微信里打字突然闪退",
           rep_conv="c1", rep_fid="f1"):
    if versions is None:
        versions = {"5.4.0": 3, "5.4.1": 9}
    return scorer.CandidateGroup(
        signature=sig, conversations=[], score=42.0,
        dup_count=dup, p0_count=p0, cross_version=cross, recent_24h=1,
        affected_versions=versions,
        representative_text=rep_text,
        representative_conv_id=rep_conv,
        representative_feedback_id=rep_fid,
    )


def test_format_empty_returns_no_topbug_message():
    text = formatter.format_message(
        push_date="2026-05-22", scanned=0, all_groups=0, top_groups=[],
    )
    assert "今日无 Top Bug" in text
    assert "2026-05-22" in text


def test_format_includes_header_with_scan_stats():
    text = formatter.format_message(
        push_date="2026-05-22", scanned=142, all_groups=28,
        top_groups=[_group()],
    )
    assert "2026-05-22" in text
    assert "142" in text
    assert "28" in text


def test_format_uses_chinese_display_name():
    text = formatter.format_message(
        push_date="2026-05-22", scanned=1, all_groups=1,
        top_groups=[_group()],
    )
    assert "闪退类" in text


def test_format_includes_severity_l2_version():
    text = formatter.format_message(
        push_date="2026-05-22", scanned=1, all_groups=1,
        top_groups=[_group(p0=1)],
    )
    assert "[P0]" in text
    assert "输入核心" in text
    assert "5.4 版本" in text


def test_format_cross_version_shows_label():
    text = formatter.format_message(
        push_date="2026-05-22", scanned=1, all_groups=1,
        top_groups=[_group(cross=1)],
    )
    assert "跨版本" in text
    assert "5.4 版本" not in text


def test_format_includes_dup_count():
    text = formatter.format_message(
        push_date="2026-05-22", scanned=1, all_groups=1,
        top_groups=[_group(dup=12)],
    )
    assert "12" in text


def test_format_uses_representative_text_in_quote():
    text = formatter.format_message(
        push_date="2026-05-22", scanned=1, all_groups=1,
        top_groups=[_group(rep_text="微信里打字突然闪退")],
    )
    assert "微信里打字突然闪退" in text


def test_format_lists_affected_versions_descending():
    text = formatter.format_message(
        push_date="2026-05-22", scanned=1, all_groups=1,
        top_groups=[_group(versions={"5.4.0": 3, "5.4.1": 9})],
    )
    pos_91 = text.index("5.4.1 (9)")
    pos_40 = text.index("5.4.0 (3)")
    assert pos_91 < pos_40


def test_format_p0_severity_takes_precedence_over_p1():
    text = formatter.format_message(
        push_date="2026-05-22", scanned=1, all_groups=1,
        top_groups=[_group(p0=2)],
    )
    assert "[P0]" in text


def test_format_p1_only_when_no_p0():
    text = formatter.format_message(
        push_date="2026-05-22", scanned=1, all_groups=1,
        top_groups=[_group(p0=0)],
    )
    assert "[P1]" in text


def test_format_multiple_groups_numbered():
    g1 = _group(sig=("crash", "输入核心", "5.4"))
    g2 = _group(sig=("input_dead", "语音", "5.4"))
    text = formatter.format_message(
        push_date="2026-05-22", scanned=2, all_groups=2,
        top_groups=[g1, g2],
    )
    assert "**1." in text
    assert "**2." in text
