"""测试 pusher.config_loader：yaml 加载 + 校验。"""
from __future__ import annotations

import pytest

from feedback_hub.pusher import config_loader


@pytest.fixture(autouse=True)
def _reset():
    config_loader.reset_cache()
    yield
    config_loader.reset_cache()


def test_load_returns_expected_top_level_keys():
    cfg = config_loader.load()
    assert set(cfg.keys()) >= {
        "groups", "specific_groups", "priority_order",
        "scoring", "display_names",
    }


def test_groups_have_keywords_and_8_specific_plus_generic():
    cfg = config_loader.load()
    assert "generic_bug" in cfg["groups"]
    for g in cfg["specific_groups"]:
        assert g in cfg["groups"]
        assert len(cfg["groups"][g]["keywords"]) > 0


def test_priority_order_only_contains_specific_groups():
    cfg = config_loader.load()
    assert set(cfg["priority_order"]) == set(cfg["specific_groups"])


def test_scoring_has_4_weights():
    cfg = config_loader.load()
    assert set(cfg["scoring"].keys()) == {
        "w_dup_count", "w_p0_count", "w_cross_version", "w_recent_24h",
    }
    for v in cfg["scoring"].values():
        assert isinstance(v, (int, float)) and v >= 0


def test_display_names_cover_all_specific_groups():
    cfg = config_loader.load()
    for g in cfg["specific_groups"]:
        assert g in cfg["display_names"]
        assert isinstance(cfg["display_names"][g], str)


def test_load_is_cached():
    a = config_loader.load()
    b = config_loader.load()
    assert a is b


def test_load_path_override(tmp_path):
    bad = tmp_path / "ok.yaml"
    bad.write_text(
        "groups:\n  generic_bug:\n    keywords: [bug]\n"
        "specific_groups: []\npriority_order: []\n"
        "scoring:\n  w_dup_count: 1.0\n  w_p0_count: 1.0\n"
        "  w_cross_version: 1.0\n  w_recent_24h: 1.0\n"
        "display_names: {}\n",
        encoding="utf-8",
    )
    cfg = config_loader.load(path=bad)
    assert cfg["specific_groups"] == []


def test_load_missing_field_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("groups: {}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        config_loader.load(path=bad)
