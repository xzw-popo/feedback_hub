"""测试 pusher.signature：assign_group 多组命中规则 + compute_signature。"""
from __future__ import annotations

import pytest

from feedback_hub.pusher import signature
from feedback_hub.pusher.config_loader import reset_cache


@pytest.fixture(autouse=True)
def _reset():
    reset_cache()
    yield
    reset_cache()


# ---------- assign_group ----------

def test_assign_group_no_keyword_returns_none():
    assert signature.assign_group("你好") is None


def test_assign_group_single_specific_hit():
    assert signature.assign_group("微信里突然闪退了") == "crash"


def test_assign_group_only_generic_returns_generic_bug():
    assert signature.assign_group("有个 bug") == "generic_bug"


def test_assign_group_generic_yields_to_specific():
    """spec §2.2 规则 1：specific + generic 同时命中 → 取 specific。"""
    assert signature.assign_group("有个 bug 一直闪退") == "crash"


def test_assign_group_priority_crash_over_lag():
    assert signature.assign_group("卡死然后闪退了") == "crash"


def test_assign_group_priority_input_dead_over_garbled():
    assert signature.assign_group("打不出字而且乱码") == "input_dead"


def test_assign_group_full_text_concat_works():
    """方案 X 关键 case：首条寒暄 + 末条带关键词的拼接文本能正确召回。"""
    full_text = "你好 || 我用 5.4.1 || 突然闪退了"
    assert signature.assign_group(full_text) == "crash"


def test_assign_group_empty_returns_none():
    assert signature.assign_group("") is None


# ---------- compute_signature ----------

def test_compute_signature_returns_tuple():
    sig = signature.compute_signature(
        full_text="一直闪退啊", L2="输入核心", appversion="5.4.1",
    )
    assert sig == ("crash", "输入核心", "5.4")


def test_compute_signature_l2_uses_first():
    sig = signature.compute_signature(
        full_text="闪退", L2="输入核心|账号", appversion="5.4.1",
    )
    assert sig[1] == "输入核心"


def test_compute_signature_l2_empty_uses_unknown():
    sig = signature.compute_signature(
        full_text="闪退", L2="", appversion="5.4.1",
    )
    assert sig[1] == "_unknown"


def test_compute_signature_appversion_none_uses_unknown():
    sig = signature.compute_signature(
        full_text="闪退", L2="输入核心", appversion=None,
    )
    assert sig[2] == "_unknown"


def test_compute_signature_appversion_no_dot_kept_as_is():
    sig = signature.compute_signature(
        full_text="闪退", L2="输入核心", appversion="5",
    )
    assert sig[2] == "5"


def test_compute_signature_returns_none_for_no_match():
    assert signature.compute_signature(
        full_text="你好啊", L2="其他", appversion="5.4.1",
    ) is None


def test_compute_signature_returns_none_for_only_generic_bug():
    assert signature.compute_signature(
        full_text="有个 bug", L2="其他", appversion="5.4.1",
    ) is None
