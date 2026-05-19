"""验证 config 模块的常量、路径与函数。"""
from __future__ import annotations

from pathlib import Path

from feedback_hub import config


def test_paths_are_pathlib_objects():
    assert isinstance(config.PKG_DIR, Path)
    assert isinstance(config.DATA_DIR, Path)
    assert isinstance(config.RAW_DIR, Path)
    assert isinstance(config.DB_PATH, Path)
    assert config.RAW_DIR.parent == config.DATA_DIR
    assert config.DB_PATH.parent == config.DATA_DIR
    assert config.DB_PATH.name == "feedback.db"


def test_label_enums_consistency():
    assert "A.Bug" in config.L1_VALUES
    assert "待定" in config.L1_VALUES
    assert len(config.L1_VALUES) == 6
    assert "其他" in config.L2_VALUES
    assert config.SEVERITY_VALUES == ["P0", "P1", "P2", "P3"]


def test_priority_dicts_cover_all_values():
    assert set(config.L1_PRIORITY.keys()) == set(config.L1_VALUES)
    assert set(config.SEVERITY_PRIORITY.keys()) == set(config.SEVERITY_VALUES)
    assert config.L1_PRIORITY["A.Bug"] == max(config.L1_PRIORITY.values())
    assert config.SEVERITY_PRIORITY["P0"] == max(config.SEVERITY_PRIORITY.values())


def test_get_agent_key_uses_env(monkeypatch):
    monkeypatch.setenv("WINK_AGENT_KEY", "my-test-key")
    assert config.get_agent_key() == "my-test-key"


def test_get_agent_key_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("WINK_AGENT_KEY", raising=False)
    assert config.get_agent_key() == config.DEFAULT_AGENT_KEY


def test_gap_seconds_is_30_minutes():
    assert config.CONVERSATION_GAP_SECONDS == 1800


def test_ensure_dirs_creates_directories(tmp_path, monkeypatch):
    fake_data = tmp_path / "data"
    fake_raw = fake_data / "raw"
    monkeypatch.setattr(config, "DATA_DIR", fake_data)
    monkeypatch.setattr(config, "RAW_DIR", fake_raw)
    config.ensure_dirs()
    assert fake_data.is_dir()
    assert fake_raw.is_dir()
