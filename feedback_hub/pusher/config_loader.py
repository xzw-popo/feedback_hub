"""kw_groups.yaml 加载 + 完整性校验 + 模块级缓存。

设计：
- `load()` 默认从 `config.KW_GROUPS_PATH` 读，结果缓存在模块全局
- `load(path=...)` 用于测试或显式覆盖；不会进入缓存
- 启动期校验：缺关键字段直接抛 ValueError，不让坏配置悄悄跑起来
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Union

import yaml

from feedback_hub import config

_REQUIRED_TOP = ("groups", "specific_groups", "priority_order",
                 "scoring", "display_names")
_REQUIRED_SCORING = ("w_dup_count", "w_p0_count",
                     "w_cross_version", "w_recent_24h")

_cache: Optional[dict] = None


def _validate(cfg: dict) -> None:
    for key in _REQUIRED_TOP:
        if key not in cfg:
            raise ValueError(f"kw_groups.yaml 缺少顶层字段: {key}")

    groups = cfg["groups"]
    if not isinstance(groups, dict):
        raise ValueError("groups 必须是 dict")

    specific = cfg["specific_groups"]
    priority = cfg["priority_order"]
    if set(priority) != set(specific):
        raise ValueError("priority_order 必须与 specific_groups 一一对应")

    for g in specific:
        if g not in groups:
            raise ValueError(f"specific_groups 中的 {g} 在 groups 里不存在")
        kws = groups[g].get("keywords") if isinstance(groups[g], dict) else None
        if not kws:
            raise ValueError(f"组 {g} 缺少 keywords 或为空")

    if "generic_bug" not in groups:
        raise ValueError("groups 中必须包含 generic_bug 组")

    scoring = cfg["scoring"]
    for w in _REQUIRED_SCORING:
        if w not in scoring:
            raise ValueError(f"scoring 缺少权重: {w}")
        if not isinstance(scoring[w], (int, float)) or scoring[w] < 0:
            raise ValueError(f"scoring.{w} 必须是非负数")

    display = cfg["display_names"]
    for g in specific:
        if g not in display:
            raise ValueError(f"display_names 缺少 {g} 的中文名")


def load(path: Union[Path, str, None] = None) -> dict:
    """加载 kw_groups.yaml；无 path 时使用全局缓存。"""
    global _cache
    if path is None:
        if _cache is None:
            with open(config.KW_GROUPS_PATH, encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            _validate(cfg)
            _cache = cfg
        return _cache

    with open(Path(path), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    _validate(cfg)
    return cfg


def reset_cache() -> None:
    """测试用：清空模块缓存。"""
    global _cache
    _cache = None
