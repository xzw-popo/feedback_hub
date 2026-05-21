# 每日 Top Bug 推送 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `feedback_hub` 现有数据中枢上新增"每日 Top 5 Bug 候选自动推送到企微群"的能力，包括关键词分组签名计算、聚合打分、Markdown 格式化、企微 webhook 推送、push_log 审计表，以及 30 天历史回灌脚本。

**Architecture:** 新增独立子包 `feedback_hub/pusher/`，纯函数式（无全局状态），从 `conversation_label` + `feedback` + `message_label` 联合查询拿候选 → Python 端实时算签名（不入库）→ 聚合打分 → 取 Top 5 → 格式化为 Markdown → POST 到企微 webhook → 落 `push_log` 表审计。`schema.sql` 追加一张 `push_log` 表；`cli.py` 追加 `push` 子命令；新增 `scripts/backfill.sh`。

**Tech Stack:** Python 3.10+ stdlib (sqlite3, argparse, dataclasses, json, hashlib, time)、PyYAML（已有）、requests（已有）、pytest（已有）。**禁止引入新依赖**。

**Spec 引用:** [`docs/superpowers/specs/2026-05-21-daily-bug-pusher-design.md`](../specs/2026-05-21-daily-bug-pusher-design.md)

---

## File Structure

新增文件：

| 路径 | 职责 |
|---|---|
| `feedback_hub/pusher/__init__.py` | 子包标识 |
| `feedback_hub/pusher/kw_groups.yaml` | 8 + 1 关键词分组、优先级、评分权重、组中文名 |
| `feedback_hub/pusher/config_loader.py` | 加载 yaml，校验完整性，缓存 |
| `feedback_hub/pusher/signature.py` | `assign_group(text)` + `compute_signature(full_text, L2, appversion)` |
| `feedback_hub/pusher/scorer.py` | `aggregate_and_score(rows, now_ms, weights)` + `pick_top5` + 数据类 |
| `feedback_hub/pusher/candidate.py` | SQL 拉取（CTE 三段） + 主编排 `generate_candidates(conn, now_ms)` |
| `feedback_hub/pusher/formatter.py` | 候选 → Markdown 字符串 |
| `feedback_hub/pusher/webhook.py` | 企微 webhook POST + 重试 |
| `feedback_hub/pusher/push_log.py` | `push_log` 表读写 helper（业务语义包装） |
| `feedback_hub/scripts/backfill.sh` | 30 天回灌 shell |
| `feedback_hub/tests/test_pusher_config.py` | yaml 加载与校验单测 |
| `feedback_hub/tests/test_signature.py` | 签名 + 多组命中规则单测 |
| `feedback_hub/tests/test_scorer.py` | 评分 + Top 5 选取单测 |
| `feedback_hub/tests/test_candidate.py` | SQL 拉取集成测 |
| `feedback_hub/tests/test_formatter.py` | Markdown 格式快照测试 |
| `feedback_hub/tests/test_webhook.py` | webhook 重试逻辑（mock requests） |
| `feedback_hub/tests/test_push_log.py` | push_log 表读写 |
| `feedback_hub/tests/test_push_cli.py` | `push` 子命令端到端 |

修改文件：

| 路径 | 改动 |
|---|---|
| `feedback_hub/schema.sql` | 追加 `push_log` 表 + 索引 |
| `feedback_hub/db.py` | 追加 `PUSH_LOG_COLUMNS` + `insert_push_log` + `update_push_log_delivered` |
| `feedback_hub/config.py` | 追加 `KW_GROUPS_PATH` + `get_webhook_url()` |
| `feedback_hub/cli.py` | 追加 `push` 子命令 |
| `feedback_hub/tests/test_db.py` | 追加 push_log schema 检查 |

---

## Task 顺序与依赖

```
T1 schema/db (push_log)    ──┐
T2 yaml + config_loader    ──┴─→ T3 signature ─→ T4 scorer ─→ T5 candidate ─→
                                                                              ├─→ T8 cli ─→ T10 backfill
T6 formatter ────────────────────────────────────────────────────────────────┤
T7 push_log helper ──────────────────────────────────────────────────────────┤
T9 webhook ──────────────────────────────────────────────────────────────────┘
```

每个任务独立可测试，按编号顺序执行最稳。

---

## Task 1: push_log 表 schema + db helper

**Files:**
- Modify: `feedback_hub/schema.sql`（追加表）
- Modify: `feedback_hub/db.py`（追加常量 + 两个 helper）
- Modify: `feedback_hub/tests/test_db.py`（追加 push_log 测试）

- [ ] **Step 1: 写 schema 测试（先红）**

把以下内容追加到 `feedback_hub/tests/test_db.py` 末尾：

```python
import time as _time


def test_push_log_table_exists(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r[0] for r in rows}
    assert "push_log" in names


def test_insert_push_log_returns_id(conn):
    pid = db.insert_push_log(conn, {
        "push_date": "2026-05-22", "rank": 1,
        "signature": "crash|输入核心|5.4",
        "group_id": "crash", "primary_l2": "输入核心", "major_version": "5.4",
        "representative_conversation_id": "c1", "representative_feedback_id": "f1",
        "score": 42.0, "dup_count": 12, "p0_count": 8, "cross_version": 1,
        "affected_versions": "5.4.0:3|5.4.1:9",
        "representative_text": "微信里打字突然闪退",
        "created_at": int(_time.time()), "delivered_at": None, "is_empty": 0,
    })
    assert isinstance(pid, int) and pid > 0


def test_update_push_log_delivered(conn):
    pid = db.insert_push_log(conn, {
        "push_date": "2026-05-22", "rank": 1, "signature": "x",
        "group_id": "x", "primary_l2": "x", "major_version": "x",
        "representative_conversation_id": "c", "representative_feedback_id": "f",
        "score": 1.0, "dup_count": 1, "p0_count": 1, "cross_version": 0,
        "affected_versions": "1.0:1", "representative_text": "t",
        "created_at": 100, "delivered_at": None, "is_empty": 0,
    })
    db.update_push_log_delivered(conn, pid, 200)
    row = conn.execute(
        "SELECT delivered_at FROM push_log WHERE id=?", (pid,),
    ).fetchone()
    assert row["delivered_at"] == 200


def test_push_log_is_empty_placeholder(conn):
    pid = db.insert_push_log(conn, {
        "push_date": "2026-05-22", "rank": 0,
        "signature": None, "group_id": None, "primary_l2": None,
        "major_version": None,
        "representative_conversation_id": None, "representative_feedback_id": None,
        "score": None, "dup_count": None, "p0_count": None, "cross_version": None,
        "affected_versions": None, "representative_text": None,
        "created_at": 100, "delivered_at": None, "is_empty": 1,
    })
    row = conn.execute(
        "SELECT is_empty, signature FROM push_log WHERE id=?", (pid,),
    ).fetchone()
    assert row["is_empty"] == 1
    assert row["signature"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/charvel/Desktop/用户反馈_2026_0519
pytest feedback_hub/tests/test_db.py::test_push_log_table_exists -v
```
Expected: FAIL —— `push_log` 表不存在

- [ ] **Step 3: schema.sql 追加 push_log 表**

把以下内容追加到 `feedback_hub/schema.sql` 末尾：

```sql

-- 5. 推送日志（spec §5.1）：每日推送的审计 + 未来 A→B 升级钩子
CREATE TABLE IF NOT EXISTS push_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    push_date       TEXT NOT NULL,
    rank            INTEGER NOT NULL,
    signature       TEXT,
    group_id        TEXT,
    primary_l2      TEXT,
    major_version   TEXT,
    representative_conversation_id TEXT,
    representative_feedback_id     TEXT,
    score           REAL,
    dup_count       INTEGER,
    p0_count        INTEGER,
    cross_version   INTEGER,
    affected_versions TEXT,
    representative_text TEXT,
    created_at      INTEGER NOT NULL,
    delivered_at    INTEGER,
    is_empty        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_pushlog_date    ON push_log(push_date);
CREATE INDEX IF NOT EXISTS idx_pushlog_sig     ON push_log(signature);
CREATE INDEX IF NOT EXISTS idx_pushlog_repconv ON push_log(representative_conversation_id);
CREATE INDEX IF NOT EXISTS idx_pushlog_repmsg  ON push_log(representative_feedback_id);
```

- [ ] **Step 4: db.py 追加 PUSH_LOG_COLUMNS 与两个 helper**

把以下代码追加到 `feedback_hub/db.py` 末尾：

```python


# ----------------------------- push_log ---------------------------------

PUSH_LOG_COLUMNS: tuple[str, ...] = (
    "push_date", "rank", "signature",
    "group_id", "primary_l2", "major_version",
    "representative_conversation_id", "representative_feedback_id",
    "score", "dup_count", "p0_count", "cross_version",
    "affected_versions", "representative_text",
    "created_at", "delivered_at", "is_empty",
)


def insert_push_log(conn: sqlite3.Connection, row: dict[str, Any]) -> int:
    """插入一条推送日志，返回自增 id。"""
    placeholders = ", ".join("?" * len(PUSH_LOG_COLUMNS))
    cols = ", ".join(PUSH_LOG_COLUMNS)
    vals = tuple(row.get(c) for c in PUSH_LOG_COLUMNS)
    cur = conn.execute(
        f"INSERT INTO push_log ({cols}) VALUES ({placeholders})", vals,
    )
    return int(cur.lastrowid)


def update_push_log_delivered(
    conn: sqlite3.Connection, push_log_id: int, delivered_at: int,
) -> None:
    """标记一条 push_log 为已送达。"""
    conn.execute(
        "UPDATE push_log SET delivered_at = ? WHERE id = ?",
        (delivered_at, push_log_id),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest feedback_hub/tests/test_db.py -v
```
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add feedback_hub/schema.sql feedback_hub/db.py feedback_hub/tests/test_db.py
git commit -m "feat(pusher): add push_log table + db helpers"
```

---

## Task 2: pusher 子包骨架 + kw_groups.yaml + config_loader

**Files:**
- Create: `feedback_hub/pusher/__init__.py`
- Create: `feedback_hub/pusher/kw_groups.yaml`
- Create: `feedback_hub/pusher/config_loader.py`
- Create: `feedback_hub/tests/test_pusher_config.py`
- Modify: `feedback_hub/config.py`

- [ ] **Step 1: 写 config_loader 测试（先红）**

创建 `feedback_hub/tests/test_pusher_config.py`：

```python
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
    bad = tmp_path / "bad.yaml"
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest feedback_hub/tests/test_pusher_config.py -v
```
Expected: FAIL —— `feedback_hub.pusher` 不存在

- [ ] **Step 3: 创建 pusher 包骨架**

创建 `feedback_hub/pusher/__init__.py`：

```python
"""每日 Top Bug 推送子包（spec 阶段 2）。"""
```

- [ ] **Step 4: 创建 kw_groups.yaml**

创建 `feedback_hub/pusher/kw_groups.yaml`：

```yaml
# kw_groups.yaml —— Bug 关键词分组 + 优先级 + 评分权重 + 显示名
# spec §2.1 / §2.2 / §3.4 / §4.1
# 修改后无需改代码，重启进程即可生效。

groups:
  crash:
    keywords: [闪退, 崩溃, crash, 打不开, 无法启动, 启动不了, 白屏, 黑屏, 自动退出, 重启]
  input_dead:
    keywords: [无法输入, 输入不了, 打不出字, 敲不了字, 失灵, 失效]
  unusable:
    keywords: [用不了, 无法使用, 完全不能用, 完全用不了]
  lag:
    keywords: [卡死, 卡住, 卡顿, 反应慢, 延迟]
  power:
    keywords: [发烫, 耗电, 掉电]
  garbled:
    keywords: [乱码, 错位, 错乱, 丢字, 漏字, 显示不全]
  missing:
    keywords: [不显示, 看不到, 消失了, 找不到]
  generic_bug:
    keywords: [bug, BUG, 故障, 异常]

specific_groups: [crash, input_dead, unusable, lag, power, garbled, missing]

priority_order: [crash, input_dead, unusable, garbled, lag, power, missing]

scoring:
  w_dup_count: 3.0
  w_p0_count: 2.0
  w_cross_version: 5.0
  w_recent_24h: 2.0

display_names:
  crash: 闪退类
  input_dead: 输入失效
  unusable: 整体不可用
  lag: 卡顿延迟
  power: 发热耗电
  garbled: 内容错乱
  missing: 元素消失
```

- [ ] **Step 5: config.py 追加路径与 webhook url**

把以下代码追加到 `feedback_hub/config.py` 末尾：

```python


# ---------- Pusher（spec 阶段 2）----------
KW_GROUPS_PATH: Path = PKG_DIR / "pusher" / "kw_groups.yaml"


def get_webhook_url() -> str | None:
    """企微群机器人 webhook URL；优先取环境变量 WECHAT_WEBHOOK_URL。"""
    return os.environ.get("WECHAT_WEBHOOK_URL")
```

- [ ] **Step 6: 实现 config_loader.py**

创建 `feedback_hub/pusher/config_loader.py`：

```python
"""kw_groups.yaml 加载 + 完整性校验 + 模块级缓存。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from feedback_hub import config

_REQUIRED_TOP = ("groups", "specific_groups", "priority_order",
                 "scoring", "display_names")
_REQUIRED_SCORING = ("w_dup_count", "w_p0_count",
                     "w_cross_version", "w_recent_24h")

_cache: dict[str, Any] | None = None


def _validate(cfg: dict[str, Any]) -> None:
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


def load(path: Path | str | None = None) -> dict[str, Any]:
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
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
pytest feedback_hub/tests/test_pusher_config.py -v
```
Expected: 8 passed

- [ ] **Step 8: Commit**

```bash
git add feedback_hub/pusher/ feedback_hub/config.py feedback_hub/tests/test_pusher_config.py
git commit -m "feat(pusher): add kw_groups.yaml + config_loader with validation"
```

---

## Task 3: signature 模块（assign_group + compute_signature）

**Files:**
- Create: `feedback_hub/pusher/signature.py`
- Create: `feedback_hub/tests/test_signature.py`

- [ ] **Step 1: 写 signature 单测（先红）**

创建 `feedback_hub/tests/test_signature.py`：

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest feedback_hub/tests/test_signature.py -v
```
Expected: FAIL —— `feedback_hub.pusher.signature` 不存在

- [ ] **Step 3: 实现 signature.py**

创建 `feedback_hub/pusher/signature.py`：

```python
"""签名计算（spec §2.2 / §2.3 / §2.5）。

签名为 None 表示该会话不参与 Top 5 聚合。
"""
from __future__ import annotations

from feedback_hub.pusher.config_loader import load


def assign_group(text: str) -> str | None:
    """根据文本命中关键词，返回一个组 ID。

    多组命中规则（spec §2.2）：
      1. 同时命中 specific + generic_bug → 丢弃 generic_bug
      2. 多个 specific 都命中 → 按 priority_order 取第一个
      3. 仅命中 generic_bug → 返回 'generic_bug'
      4. 都没命中 → 返回 None
    """
    if not text:
        return None
    cfg = load()
    groups = cfg["groups"]
    specific_set = set(cfg["specific_groups"])
    priority = cfg["priority_order"]

    hits: set[str] = set()
    for gid, g in groups.items():
        for kw in g["keywords"]:
            if kw and kw in text:
                hits.add(gid)
                break

    if "generic_bug" in hits and (hits & specific_set):
        hits.discard("generic_bug")

    for g in priority:
        if g in hits:
            return g

    if hits == {"generic_bug"}:
        return "generic_bug"

    return None


def compute_signature(
    *, full_text: str, L2: str | None, appversion: str | None,
) -> tuple[str, str, str] | None:
    """根据 §2.5 算签名。

    Args:
        full_text:  会话全部用户消息按 msg_seq 升序拼接的文本
        L2:         conversation_label.L2，'|' 分隔字符串
        appversion: conversation_label.appversion

    Returns:
        三元组 (group_id, primary_l2, major_version) 或 None
    """
    group = assign_group(full_text or "")
    if group is None or group == "generic_bug":
        return None

    primary_l2 = (L2.split("|", 1)[0].strip() if L2 else "") or "_unknown"

    if appversion:
        if "." in appversion:
            major_version = appversion.rsplit(".", 1)[0]
        else:
            major_version = appversion
    else:
        major_version = "_unknown"

    return (group, primary_l2, major_version)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest feedback_hub/tests/test_signature.py -v
```
Expected: 15 passed

- [ ] **Step 5: Commit**

```bash
git add feedback_hub/pusher/signature.py feedback_hub/tests/test_signature.py
git commit -m "feat(pusher): add signature.py with multi-group hit rules"
```

---

## Task 4: scorer 模块（数据类 + 聚合打分 + Top 5）

**Files:**
- Create: `feedback_hub/pusher/scorer.py`
- Create: `feedback_hub/tests/test_scorer.py`

- [ ] **Step 1: 写 scorer 测试（先红）**

创建 `feedback_hub/tests/test_scorer.py`：

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest feedback_hub/tests/test_scorer.py -v
```
Expected: FAIL —— `feedback_hub.pusher.scorer` 不存在

- [ ] **Step 3: 实现 scorer.py**

创建 `feedback_hub/pusher/scorer.py`：

```python
"""聚合打分 + Top 5 选取（spec §3.3 / §3.4 / §3.5）。"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from feedback_hub.pusher.signature import compute_signature

ONE_DAY_MS = 86_400_000


@dataclass
class ConversationRow:
    conversation_id: str
    L1: str
    L2: str
    severity: str
    confidence: float
    appversion: str | None
    last_ts_ms: int
    full_text: str
    top_conf_text: str
    top_conf_feedback_id: str


@dataclass
class CandidateGroup:
    signature: tuple[str, str, str]
    conversations: list[ConversationRow]
    score: float = 0.0
    dup_count: int = 0
    p0_count: int = 0
    cross_version: int = 0
    recent_24h: int = 0
    affected_versions: dict[str, int] = field(default_factory=dict)
    representative_text: str = ""
    representative_conv_id: str = ""
    representative_feedback_id: str = ""


def _affected_versions(convs: list[ConversationRow]) -> dict[str, int]:
    counter: dict[str, int] = {}
    for c in convs:
        v = c.appversion or "_unknown"
        counter[v] = counter.get(v, 0) + 1
    return counter


def aggregate_and_score(
    rows: Iterable[ConversationRow],
    *, now_ms: int, weights: dict[str, float],
) -> list[CandidateGroup]:
    """对一批候选会话按签名聚合并打分，返回降序排列的 CandidateGroup 列表。"""
    buckets: dict[tuple[str, str, str], list[ConversationRow]] = defaultdict(list)
    for r in rows:
        sig = compute_signature(
            full_text=r.full_text, L2=r.L2, appversion=r.appversion,
        )
        if sig is None:
            continue
        buckets[sig].append(r)

    groups: list[CandidateGroup] = []
    for sig, convs in buckets.items():
        dup_count = len(convs)
        p0_count = sum(1 for c in convs if c.severity == "P0")
        versions = {c.appversion for c in convs if c.appversion}
        cross_version = 1 if len(versions) > 1 else 0
        recent_24h = 1 if any(
            c.last_ts_ms >= now_ms - ONE_DAY_MS for c in convs
        ) else 0

        score = (
            dup_count     * weights["w_dup_count"]
          + p0_count      * weights["w_p0_count"]
          + cross_version * weights["w_cross_version"]
          + recent_24h    * weights["w_recent_24h"]
        )

        rep = max(convs, key=lambda c: c.confidence)
        rep_text = rep.top_conf_text or (rep.full_text or "")[:100]

        groups.append(CandidateGroup(
            signature=sig, conversations=convs, score=score,
            dup_count=dup_count, p0_count=p0_count,
            cross_version=cross_version, recent_24h=recent_24h,
            affected_versions=_affected_versions(convs),
            representative_text=rep_text,
            representative_conv_id=rep.conversation_id,
            representative_feedback_id=rep.top_conf_feedback_id,
        ))

    groups.sort(key=lambda g: g.score, reverse=True)
    return groups


def pick_top5(groups: list[CandidateGroup]) -> list[CandidateGroup]:
    """取前 5；不足 5 个就有几个返几个。"""
    return groups[:5]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest feedback_hub/tests/test_scorer.py -v
```
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add feedback_hub/pusher/scorer.py feedback_hub/tests/test_scorer.py
git commit -m "feat(pusher): add scorer with aggregate_and_score + pick_top5"
```

---

## Task 5: candidate 模块（SQL 拉取 + 编排）

**Files:**
- Create: `feedback_hub/pusher/candidate.py`
- Create: `feedback_hub/tests/test_candidate.py`

- [ ] **Step 1: 写 candidate 集成测（先红）**

创建 `feedback_hub/tests/test_candidate.py`：

```python
"""测试 pusher.candidate：SQL 拉取 + 与 scorer 串通。

集成性质——用临时 sqlite + 真实 schema，seed 几条 feedback /
message_label / conversation_label，验证 generate_candidates 能正确产出。
"""
from __future__ import annotations

import time

import pytest

from feedback_hub import db
from feedback_hub.pusher import candidate
from feedback_hub.pusher.config_loader import reset_cache


@pytest.fixture(autouse=True)
def _reset():
    reset_cache()
    yield
    reset_cache()


@pytest.fixture
def conn(tmp_path, monkeypatch):
    db_path = tmp_path / "f.db"
    monkeypatch.setattr("feedback_hub.config.DB_PATH", db_path)
    c = db.connect(db_path)
    db.init_schema(c)
    try:
        yield c
    finally:
        c.close()


def _seed_conv(conn, conv_id, *, msgs, l1="A.Bug", severity="P0",
               confidence=0.9, l2="输入核心", appversion="5.4.1",
               last_ts_ms=None, ts_base=2_000_000_000_000):
    """seed 一个会话的 feedback + message_label + conversation_label。

    msgs: list[(seq, text, msg_confidence)]
    """
    now = int(time.time())
    for seq, text, mconf in msgs:
        fid = f"{conv_id}-{seq}"
        ts_ms = ts_base + seq * 1000
        db.upsert_feedback(conn, {
            "feedback_id": fid, "conversation_id": conv_id, "msg_seq": seq,
            "channel": "wetype", "ts_ms": ts_ms,
            "platform": "iOS", "appversion": appversion,
            "user_vid": f"u-{conv_id}",
            "keyboard_source": "", "device_name": "", "channelid": "",
            "enginever": "", "msgtype": "text", "text": text,
            "tags": "", "raw_json": "{}", "pulled_at": now,
        })
        db.upsert_message_label(conn, {
            "feedback_id": fid, "L1": l1, "L2": l2, "severity": severity,
            "confidence": mconf, "reason": "test",
            "source": "rule", "rule_name": None, "tagged_at": now,
        })
    final_last_ts = (last_ts_ms if last_ts_ms is not None
                     else (ts_base + (len(msgs) - 1) * 1000))
    db.upsert_conversation_label(conn, {
        "conversation_id": conv_id, "L1": l1, "L2": l2,
        "severity": severity, "confidence": confidence,
        "reason": "test", "source": "aggregated",
        "msg_count": len(msgs),
        "first_ts_ms": ts_base, "last_ts_ms": final_last_ts,
        "user_vid": f"u-{conv_id}", "appversion": appversion,
        "channel": "wetype", "aggregated_at": now,
    })
    conn.commit()


def test_no_data_returns_empty(conn):
    out = candidate.generate_candidates(conn, now_ms=2_000_000_000_000)
    assert out["scanned"] == 0
    assert out["top_groups"] == []


def test_filters_non_bug_l1(conn):
    _seed_conv(conn, "c1", msgs=[(0, "闪退", 0.9)], l1="B.建议")
    out = candidate.generate_candidates(conn, now_ms=2_000_000_000_000)
    assert out["scanned"] == 0


def test_filters_low_severity(conn):
    _seed_conv(conn, "c1", msgs=[(0, "闪退", 0.9)], severity="P2")
    out = candidate.generate_candidates(conn, now_ms=2_000_000_000_000)
    assert out["scanned"] == 0


def test_filters_low_confidence(conn):
    _seed_conv(conn, "c1", msgs=[(0, "闪退", 0.9)], confidence=0.5)
    out = candidate.generate_candidates(conn, now_ms=2_000_000_000_000)
    assert out["scanned"] == 0


def test_filters_outside_7day_window(conn):
    now_ms = 2_000_000_000_000
    eight_days_ago = now_ms - 8 * 86_400_000
    _seed_conv(conn, "c1", msgs=[(0, "闪退", 0.9)],
               last_ts_ms=eight_days_ago, ts_base=eight_days_ago)
    out = candidate.generate_candidates(conn, now_ms=now_ms)
    assert out["scanned"] == 0


def test_full_text_concat_recovers_recall(conn):
    """方案 X 关键 case：首条无关键词，第三条有 → full_text 拼接命中 crash。"""
    _seed_conv(conn, "c1", msgs=[
        (0, "你好", 0.7),
        (1, "我用 5.4.1", 0.6),
        (2, "突然闪退了", 0.95),
    ])
    out = candidate.generate_candidates(conn, now_ms=2_000_000_000_000)
    assert out["scanned"] == 1
    assert len(out["top_groups"]) == 1
    g = out["top_groups"][0]
    assert g.signature == ("crash", "输入核心", "5.4")


def test_representative_text_is_top_confidence_message(conn):
    """方案 X 关键 case：representative_text 应是 confidence 最高那条原文。"""
    _seed_conv(conn, "c1", msgs=[
        (0, "你好", 0.7),
        (1, "我用 5.4.1", 0.6),
        (2, "突然闪退了", 0.95),
    ])
    out = candidate.generate_candidates(conn, now_ms=2_000_000_000_000)
    g = out["top_groups"][0]
    assert g.representative_text == "突然闪退了"
    assert g.representative_feedback_id == "c1-2"


def test_two_convs_same_signature_aggregate(conn):
    _seed_conv(conn, "a", msgs=[(0, "闪退啊", 0.9)], appversion="5.4.0")
    _seed_conv(conn, "b", msgs=[(0, "一直闪退", 0.9)], appversion="5.4.1")
    out = candidate.generate_candidates(conn, now_ms=2_000_000_000_000)
    assert len(out["top_groups"]) == 1
    g = out["top_groups"][0]
    assert g.dup_count == 2
    assert g.cross_version == 1


def test_truncates_to_top_5(conn):
    base = 2_000_000_000_000
    for i, l2 in enumerate(["输入核心", "语音", "皮肤", "词库", "账号", "性能"]):
        _seed_conv(conn, f"c{i}", msgs=[(0, "闪退", 0.9)],
                   l2=l2, appversion="5.4.0", ts_base=base)
    out = candidate.generate_candidates(conn, now_ms=base)
    assert len(out["top_groups"]) == 5
    assert out["scanned"] == 6
    assert out["all_groups_count"] == 6
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest feedback_hub/tests/test_candidate.py -v
```
Expected: FAIL —— `feedback_hub.pusher.candidate` 不存在

- [ ] **Step 3: 实现 candidate.py**

创建 `feedback_hub/pusher/candidate.py`：

```python
"""候选生成主编排（spec §3.1 / §3.2）。

职责：
    1. 从 conversation_label + feedback + message_label 联合查询候选会话
    2. SQL 端用 CTE 拼接：
       - bug_convs：满足 L1=A.Bug + severity∈{P0,P1} + confidence≥0.7 + 7 天内
       - full_text_per_conv：每个 conv 的全部消息文本按 msg_seq 拼接
       - top_conf_msg：每个 conv 内 confidence 最高的那条 message
    3. 把 SQL 结果映射成 ConversationRow → 喂给 scorer.aggregate_and_score
    4. 返回 dict：{scanned, top_groups, all_groups_count}

只读数据库；push_log 写入由 cli.py 编排。
"""
from __future__ import annotations

import sqlite3

from feedback_hub.pusher import scorer
from feedback_hub.pusher.config_loader import load

SEVEN_DAYS_MS = 7 * 86_400_000


_CANDIDATE_SQL = """
WITH bug_convs AS (
    SELECT cl.conversation_id, cl.L1, cl.L2, cl.severity, cl.confidence,
           cl.reason, cl.msg_count, cl.first_ts_ms, cl.last_ts_ms,
           cl.user_vid, cl.appversion, cl.channel
    FROM conversation_label cl
    WHERE cl.L1 = 'A.Bug'
      AND cl.severity IN ('P0', 'P1')
      AND cl.confidence >= 0.7
      AND cl.last_ts_ms >= ?
),
full_text_per_conv AS (
    SELECT f.conversation_id,
           GROUP_CONCAT(f.text, ' || ') AS full_text
    FROM (SELECT conversation_id, text, msg_seq FROM feedback
          WHERE conversation_id IN (SELECT conversation_id FROM bug_convs)
          ORDER BY conversation_id, msg_seq) f
    GROUP BY f.conversation_id
),
top_conf_msg AS (
    SELECT t.feedback_id, t.conversation_id, t.text AS top_conf_text
    FROM (
        SELECT ml.feedback_id, f.conversation_id, f.text, ml.confidence,
               ROW_NUMBER() OVER (
                   PARTITION BY f.conversation_id
                   ORDER BY ml.confidence DESC, f.msg_seq ASC
               ) AS rn
        FROM message_label ml
        JOIN feedback f ON f.feedback_id = ml.feedback_id
        WHERE f.conversation_id IN (SELECT conversation_id FROM bug_convs)
    ) t
    WHERE t.rn = 1
)
SELECT bc.conversation_id, bc.L1, bc.L2, bc.severity, bc.confidence,
       bc.appversion, bc.last_ts_ms,
       ft.full_text,
       tc.feedback_id AS top_conf_feedback_id,
       tc.top_conf_text
FROM bug_convs bc
LEFT JOIN full_text_per_conv ft ON ft.conversation_id = bc.conversation_id
LEFT JOIN top_conf_msg tc ON tc.conversation_id = bc.conversation_id
ORDER BY bc.last_ts_ms DESC
"""


def _row_to_conv(row: sqlite3.Row) -> scorer.ConversationRow:
    return scorer.ConversationRow(
        conversation_id=row["conversation_id"],
        L1=row["L1"],
        L2=row["L2"] or "",
        severity=row["severity"],
        confidence=float(row["confidence"] or 0.0),
        appversion=row["appversion"],
        last_ts_ms=int(row["last_ts_ms"]),
        full_text=row["full_text"] or "",
        top_conf_text=row["top_conf_text"] or "",
        top_conf_feedback_id=row["top_conf_feedback_id"] or "",
    )


def generate_candidates(
    conn: sqlite3.Connection, *, now_ms: int,
) -> dict:
    """从数据库生成 Top 5 候选。

    Returns:
        {
            "scanned":          int,                      # 进入 scorer 的会话数
            "top_groups":       list[CandidateGroup],     # 至多 5 个
            "all_groups_count": int,                      # 截断前的桶数
        }
    """
    cfg = load()
    weights = cfg["scoring"]
    seven_days_ago_ms = now_ms - SEVEN_DAYS_MS

    cur = conn.execute(_CANDIDATE_SQL, (seven_days_ago_ms,))
    rows = cur.fetchall()
    convs = [_row_to_conv(r) for r in rows]

    all_groups = scorer.aggregate_and_score(
        convs, now_ms=now_ms, weights=weights,
    )
    top = scorer.pick_top5(all_groups)

    return {
        "scanned": len(convs),
        "top_groups": top,
        "all_groups_count": len(all_groups),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest feedback_hub/tests/test_candidate.py -v
```
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add feedback_hub/pusher/candidate.py feedback_hub/tests/test_candidate.py
git commit -m "feat(pusher): add candidate.py with CTE-based SQL extraction"
```

---

## Task 6: formatter 模块（候选 → Markdown）

**Files:**
- Create: `feedback_hub/pusher/formatter.py`
- Create: `feedback_hub/tests/test_formatter.py`

- [ ] **Step 1: 写 formatter 测试（先红）**

创建 `feedback_hub/tests/test_formatter.py`：

```python
"""测试 pusher.formatter：候选 → Markdown 字符串（spec §4.1）。"""
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest feedback_hub/tests/test_formatter.py -v
```
Expected: FAIL —— `feedback_hub.pusher.formatter` 不存在

- [ ] **Step 3: 实现 formatter.py**

创建 `feedback_hub/pusher/formatter.py`：

```python
"""候选 → 企微 Markdown 消息体（spec §4.1）。"""
from __future__ import annotations

from feedback_hub.pusher import scorer
from feedback_hub.pusher.config_loader import load


def _affected_versions_text(versions: dict[str, int]) -> str:
    items = sorted(versions.items(), key=lambda kv: (-kv[1], kv[0]))
    return ", ".join(f"{v} ({c})" for v, c in items)


def _severity_label(group: scorer.CandidateGroup) -> str:
    return "[P0]" if group.p0_count > 0 else "[P1]"


def _version_label(group: scorer.CandidateGroup) -> str:
    if group.cross_version:
        return "跨版本"
    return f"{group.signature[2]} 版本"


def _format_one(rank: int, group: scorer.CandidateGroup,
                display_names: dict[str, str]) -> str:
    sev = _severity_label(group)
    name_cn = display_names.get(group.signature[0], group.signature[0])
    primary_l2 = group.signature[1]
    ver_label = _version_label(group)
    quote = group.representative_text or "（无代表文本）"
    versions = _affected_versions_text(group.affected_versions)
    return (
        f"**{rank}. {sev} {name_cn} · {primary_l2} · {ver_label}** · "
        f"重复 {group.dup_count} 次\n"
        f"> \"{quote}\"\n"
        f"> 涉及版本：{versions}"
    )


def format_message(
    *, push_date: str, scanned: int, all_groups: int,
    top_groups: list[scorer.CandidateGroup],
) -> str:
    """生成完整 Markdown 消息体。"""
    cfg = load()
    display_names = cfg["display_names"]

    if not top_groups:
        return (
            f"## 📊 今日无 Top Bug · {push_date}\n\n"
            f"> 数据窗口：过去 7 天 · 扫描 {scanned} 个 Bug 会话\n\n"
            f"---\n🤖 by feedback_hub"
        )

    header = (
        f"## 📊 今日 Top Bug 候选 · {push_date}\n\n"
        f"> 数据窗口：过去 7 天 · 扫描 {scanned} 个 Bug 会话 · "
        f"命中 {all_groups} 个签名"
    )

    body = "\n\n".join(
        _format_one(i + 1, g, display_names) for i, g in enumerate(top_groups)
    )

    return (
        f"{header}\n\n---\n\n{body}\n\n---\n"
        f"🤖 by feedback_hub · 反馈或建议请联系 @charvel"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest feedback_hub/tests/test_formatter.py -v
```
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add feedback_hub/pusher/formatter.py feedback_hub/tests/test_formatter.py
git commit -m "feat(pusher): add Markdown formatter for top groups"
```

---

## Task 7: push_log helper（业务语义包装）

**Files:**
- Create: `feedback_hub/pusher/push_log.py`
- Create: `feedback_hub/tests/test_push_log.py`

把 `CandidateGroup` 转成 `push_log` 表行，并提供"批量落库"业务语义。`db.insert_push_log` 是底层；这里是业务层。

- [ ] **Step 1: 写 push_log 业务测（先红）**

创建 `feedback_hub/tests/test_push_log.py`：

```python
"""测试 pusher.push_log：把 CandidateGroup 落 push_log 表的业务语义。"""
from __future__ import annotations

import pytest

from feedback_hub import db
from feedback_hub.pusher import push_log, scorer


@pytest.fixture
def conn(tmp_path, monkeypatch):
    db_path = tmp_path / "f.db"
    monkeypatch.setattr("feedback_hub.config.DB_PATH", db_path)
    c = db.connect(db_path)
    db.init_schema(c)
    try:
        yield c
    finally:
        c.close()


def _group(sig=("crash", "输入核心", "5.4"), *, dup=12, p0=8,
           cross=1, versions=None, rep_text="t",
           rep_conv="c1", rep_fid="f1", score=42.0):
    if versions is None:
        versions = {"5.4.0": 3, "5.4.1": 9}
    return scorer.CandidateGroup(
        signature=sig, conversations=[], score=score,
        dup_count=dup, p0_count=p0, cross_version=cross, recent_24h=1,
        affected_versions=versions,
        representative_text=rep_text,
        representative_conv_id=rep_conv,
        representative_feedback_id=rep_fid,
    )


def test_save_top_groups_returns_ids_in_order(conn):
    groups = [_group(sig=("crash", "输入核心", "5.4")),
              _group(sig=("lag", "性能", "5.4"))]
    ids = push_log.save_top_groups(
        conn, push_date="2026-05-22",
        top_groups=groups, created_at=100,
    )
    assert len(ids) == 2
    rows = conn.execute(
        "SELECT id, rank, signature, group_id, primary_l2, major_version, "
        "score, dup_count, p0_count, cross_version, "
        "affected_versions, representative_text, "
        "representative_conversation_id, representative_feedback_id, "
        "created_at, delivered_at, is_empty "
        "FROM push_log ORDER BY rank ASC"
    ).fetchall()
    assert [r["rank"] for r in rows] == [1, 2]
    assert rows[0]["signature"] == "crash|输入核心|5.4"
    assert rows[0]["group_id"] == "crash"
    assert rows[0]["primary_l2"] == "输入核心"
    assert rows[0]["major_version"] == "5.4"
    assert rows[0]["dup_count"] == 12
    assert rows[0]["p0_count"] == 8
    assert rows[0]["cross_version"] == 1
    assert rows[0]["created_at"] == 100
    assert rows[0]["delivered_at"] is None
    assert rows[0]["is_empty"] == 0


def test_save_top_groups_serializes_affected_versions(conn):
    groups = [_group(versions={"5.4.0": 3, "5.4.1": 9})]
    push_log.save_top_groups(
        conn, push_date="2026-05-22", top_groups=groups, created_at=100,
    )
    row = conn.execute(
        "SELECT affected_versions FROM push_log"
    ).fetchone()
    # 按 count 降序：5.4.1:9 在前
    assert row["affected_versions"] == "5.4.1:9|5.4.0:3"


def test_save_empty_inserts_placeholder(conn):
    ids = push_log.save_top_groups(
        conn, push_date="2026-05-22", top_groups=[], created_at=100,
    )
    assert len(ids) == 1
    row = conn.execute(
        "SELECT rank, signature, is_empty, representative_text "
        "FROM push_log WHERE id=?", (ids[0],),
    ).fetchone()
    assert row["rank"] == 0
    assert row["is_empty"] == 1
    assert row["signature"] is None


def test_mark_delivered_updates_all_ids(conn):
    groups = [_group(sig=("crash", "a", "1")),
              _group(sig=("lag", "b", "1"))]
    ids = push_log.save_top_groups(
        conn, push_date="2026-05-22", top_groups=groups, created_at=100,
    )
    push_log.mark_delivered(conn, ids, delivered_at=200)
    rows = conn.execute(
        "SELECT delivered_at FROM push_log ORDER BY id"
    ).fetchall()
    assert all(r["delivered_at"] == 200 for r in rows)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest feedback_hub/tests/test_push_log.py -v
```
Expected: FAIL —— `feedback_hub.pusher.push_log` 不存在

- [ ] **Step 3: 实现 push_log.py**

创建 `feedback_hub/pusher/push_log.py`：

```python
"""push_log 表的业务语义包装层（spec §5.1 / §5.2）。

`db.py` 是底层（行级 insert/update）；这里是业务层（CandidateGroup → 多行）。
"""
from __future__ import annotations

import sqlite3

from feedback_hub import db
from feedback_hub.pusher import scorer


def _serialize_versions(versions: dict[str, int]) -> str:
    """{'5.4.0': 3, '5.4.1': 9} → '5.4.1:9|5.4.0:3'（按 count 降序）。"""
    items = sorted(versions.items(), key=lambda kv: (-kv[1], kv[0]))
    return "|".join(f"{v}:{c}" for v, c in items)


def _group_to_row(rank: int, group: scorer.CandidateGroup,
                  push_date: str, created_at: int) -> dict:
    g_id, primary_l2, major_ver = group.signature
    return {
        "push_date": push_date,
        "rank": rank,
        "signature": f"{g_id}|{primary_l2}|{major_ver}",
        "group_id": g_id,
        "primary_l2": primary_l2,
        "major_version": major_ver,
        "representative_conversation_id": group.representative_conv_id,
        "representative_feedback_id": group.representative_feedback_id,
        "score": group.score,
        "dup_count": group.dup_count,
        "p0_count": group.p0_count,
        "cross_version": group.cross_version,
        "affected_versions": _serialize_versions(group.affected_versions),
        "representative_text": group.representative_text,
        "created_at": created_at,
        "delivered_at": None,
        "is_empty": 0,
    }


def _empty_placeholder_row(push_date: str, created_at: int) -> dict:
    return {
        "push_date": push_date,
        "rank": 0,
        "signature": None,
        "group_id": None,
        "primary_l2": None,
        "major_version": None,
        "representative_conversation_id": None,
        "representative_feedback_id": None,
        "score": None,
        "dup_count": None,
        "p0_count": None,
        "cross_version": None,
        "affected_versions": None,
        "representative_text": None,
        "created_at": created_at,
        "delivered_at": None,
        "is_empty": 1,
    }


def save_top_groups(
    conn: sqlite3.Connection, *,
    push_date: str,
    top_groups: list[scorer.CandidateGroup],
    created_at: int,
) -> list[int]:
    """把 Top 5 落 push_log；候选为空时插一条 is_empty=1 占位行。

    Returns:
        本次插入的 push_log id 列表（顺序与 top_groups 一致；空候选时长度=1）
    """
    ids: list[int] = []
    if not top_groups:
        pid = db.insert_push_log(
            conn, _empty_placeholder_row(push_date, created_at),
        )
        ids.append(pid)
    else:
        for i, g in enumerate(top_groups):
            pid = db.insert_push_log(
                conn, _group_to_row(i + 1, g, push_date, created_at),
            )
            ids.append(pid)
    conn.commit()
    return ids


def mark_delivered(
    conn: sqlite3.Connection, push_log_ids: list[int], *, delivered_at: int,
) -> None:
    """批量把一组 push_log 标记为已送达。"""
    for pid in push_log_ids:
        db.update_push_log_delivered(conn, pid, delivered_at)
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest feedback_hub/tests/test_push_log.py -v
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add feedback_hub/pusher/push_log.py feedback_hub/tests/test_push_log.py
git commit -m "feat(pusher): add push_log business helper (CandidateGroup → table rows)"
```

---

## Task 8: webhook 模块（企微 POST + 重试）

**Files:**
- Create: `feedback_hub/pusher/webhook.py`
- Create: `feedback_hub/tests/test_webhook.py`

- [ ] **Step 1: 写 webhook 测试（先红）**

创建 `feedback_hub/tests/test_webhook.py`：

```python
"""测试 pusher.webhook：POST + 重试 + 失败语义。"""
from __future__ import annotations

import pytest

from feedback_hub.pusher import webhook


class _FakeResp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {"errcode": 0, "errmsg": "ok"}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


def test_send_success_no_retry(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append((url, json))
        return _FakeResp(200)

    monkeypatch.setattr("feedback_hub.pusher.webhook.requests.post", fake_post)
    monkeypatch.setattr("feedback_hub.pusher.webhook.time.sleep",
                        lambda s: None)

    ok = webhook.send_markdown(
        webhook_url="https://x", markdown="# hi", max_retries=3,
    )
    assert ok is True
    assert len(calls) == 1


def test_send_retries_then_succeeds(monkeypatch):
    counter = {"n": 0}

    def fake_post(url, json=None, timeout=None):
        counter["n"] += 1
        if counter["n"] < 3:
            raise webhook.requests.RequestException("network")
        return _FakeResp(200)

    monkeypatch.setattr("feedback_hub.pusher.webhook.requests.post", fake_post)
    monkeypatch.setattr("feedback_hub.pusher.webhook.time.sleep",
                        lambda s: None)

    ok = webhook.send_markdown(
        webhook_url="https://x", markdown="# hi", max_retries=3,
    )
    assert ok is True
    assert counter["n"] == 3


def test_send_all_retries_fail(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        raise webhook.requests.RequestException("boom")

    monkeypatch.setattr("feedback_hub.pusher.webhook.requests.post", fake_post)
    monkeypatch.setattr("feedback_hub.pusher.webhook.time.sleep",
                        lambda s: None)

    ok = webhook.send_markdown(
        webhook_url="https://x", markdown="# hi", max_retries=3,
    )
    assert ok is False


def test_send_returns_false_on_errcode_non_zero(monkeypatch):
    """企微返回 200 但 errcode != 0 也算失败。"""
    def fake_post(url, json=None, timeout=None):
        return _FakeResp(200, body={"errcode": 93000, "errmsg": "invalid"})

    monkeypatch.setattr("feedback_hub.pusher.webhook.requests.post", fake_post)
    monkeypatch.setattr("feedback_hub.pusher.webhook.time.sleep",
                        lambda s: None)

    ok = webhook.send_markdown(
        webhook_url="https://x", markdown="# hi", max_retries=2,
    )
    assert ok is False


def test_send_payload_shape(monkeypatch):
    payloads = []

    def fake_post(url, json=None, timeout=None):
        payloads.append(json)
        return _FakeResp(200)

    monkeypatch.setattr("feedback_hub.pusher.webhook.requests.post", fake_post)
    monkeypatch.setattr("feedback_hub.pusher.webhook.time.sleep",
                        lambda s: None)

    webhook.send_markdown(webhook_url="https://x", markdown="# hi")
    assert payloads[0]["msgtype"] == "markdown"
    assert payloads[0]["markdown"]["content"] == "# hi"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest feedback_hub/tests/test_webhook.py -v
```
Expected: FAIL —— `feedback_hub.pusher.webhook` 不存在

- [ ] **Step 3: 实现 webhook.py**

创建 `feedback_hub/pusher/webhook.py`：

```python
"""企微群机器人 webhook 客户端（spec §4.2）。

POST 失败时按指数退避重试 (1s / 2s / 4s)。
错误日志写到 stderr；不抛异常给上层（让上层根据返回值决定是否落 delivered_at）。
"""
from __future__ import annotations

import sys
import time

import requests

DEFAULT_TIMEOUT = 15.0


def send_markdown(
    *, webhook_url: str, markdown: str, max_retries: int = 3,
    timeout: float = DEFAULT_TIMEOUT,
) -> bool:
    """POST markdown 消息到企微 webhook。

    Returns:
        True  = 至少一次返回 HTTP 200 且 errcode==0
        False = 所有重试都失败 / errcode != 0 / 网络异常
    """
    payload = {
        "msgtype": "markdown",
        "markdown": {"content": markdown},
    }

    last_err: str | None = None
    for attempt in range(max_retries):
        try:
            resp = requests.post(webhook_url, json=payload, timeout=timeout)
            if resp.status_code == 200:
                try:
                    body = resp.json()
                except Exception:
                    body = {"errcode": -1, "errmsg": "non-json response"}
                if body.get("errcode") == 0:
                    return True
                last_err = (
                    f"errcode={body.get('errcode')} "
                    f"errmsg={body.get('errmsg')}"
                )
            else:
                last_err = f"HTTP {resp.status_code}"
        except requests.RequestException as e:
            last_err = f"RequestException: {e}"
        except Exception as e:  # 防御性
            last_err = f"Unexpected: {type(e).__name__}: {e}"

        if attempt < max_retries - 1:
            backoff = 2 ** attempt  # 1s, 2s, 4s
            print(
                f"[webhook] attempt {attempt + 1} failed: {last_err}; "
                f"sleeping {backoff}s",
                file=sys.stderr,
            )
            time.sleep(backoff)

    print(f"[webhook] all {max_retries} attempts failed: {last_err}",
          file=sys.stderr)
    return False
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest feedback_hub/tests/test_webhook.py -v
```
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add feedback_hub/pusher/webhook.py feedback_hub/tests/test_webhook.py
git commit -m "feat(pusher): add webhook client with exponential backoff"
```

---

## Task 9: cli `push` 子命令（端到端编排）

**Files:**
- Modify: `feedback_hub/cli.py`（追加 `cmd_push` + 子命令注册）
- Create: `feedback_hub/tests/test_push_cli.py`

- [ ] **Step 1: 写 cli 测试（先红）**

创建 `feedback_hub/tests/test_push_cli.py`：

```python
"""端到端测：cli push 子命令的三种模式（dry-run / no-send / 真实发送）。"""
from __future__ import annotations

import time

import pytest

from feedback_hub import cli, db


@pytest.fixture(autouse=True)
def _patch_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("feedback_hub.config.DB_PATH", tmp_path / "fb.db")
    monkeypatch.setattr("feedback_hub.config.DATA_DIR", tmp_path)
    monkeypatch.setattr("feedback_hub.config.RAW_DIR", tmp_path / "raw")


@pytest.fixture(autouse=True)
def _reset_yaml_cache():
    from feedback_hub.pusher.config_loader import reset_cache
    reset_cache()
    yield
    reset_cache()


def _seed_one_p0_conv(conn, conv_id="c1", text="一直闪退",
                     appversion="5.4.1", ts_ms=None):
    """seed 一个能进 Top 5 候选的 P0 会话。"""
    if ts_ms is None:
        ts_ms = int(time.time() * 1000)
    now = int(time.time())
    db.upsert_feedback(conn, {
        "feedback_id": f"{conv_id}-0",
        "conversation_id": conv_id, "msg_seq": 0,
        "channel": "wetype", "ts_ms": ts_ms,
        "platform": "iOS", "appversion": appversion, "user_vid": "u",
        "keyboard_source": "", "device_name": "", "channelid": "",
        "enginever": "", "msgtype": "text", "text": text,
        "tags": "", "raw_json": "{}", "pulled_at": now,
    })
    db.upsert_message_label(conn, {
        "feedback_id": f"{conv_id}-0", "L1": "A.Bug", "L2": "输入核心",
        "severity": "P0", "confidence": 0.9, "reason": "test",
        "source": "rule", "rule_name": None, "tagged_at": now,
    })
    db.upsert_conversation_label(conn, {
        "conversation_id": conv_id, "L1": "A.Bug", "L2": "输入核心",
        "severity": "P0", "confidence": 0.9, "reason": "test",
        "source": "aggregated", "msg_count": 1,
        "first_ts_ms": ts_ms, "last_ts_ms": ts_ms,
        "user_vid": "u", "appversion": appversion,
        "channel": "wetype", "aggregated_at": now,
    })
    conn.commit()


def test_push_dry_run_no_db_write(capsys):
    """dry-run 既不写 push_log 也不发送。"""
    conn = db.connect()
    db.init_schema(conn)
    _seed_one_p0_conv(conn)
    conn.close()

    rc = cli.main(["push", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Top Bug" in out or "今日无 Top Bug" in out

    conn = db.connect()
    n = conn.execute("SELECT COUNT(*) FROM push_log").fetchone()[0]
    conn.close()
    assert n == 0


def test_push_no_send_writes_log_no_webhook(monkeypatch, capsys):
    """--no-send 写 push_log，但不调 webhook。"""
    conn = db.connect()
    db.init_schema(conn)
    _seed_one_p0_conv(conn)
    conn.close()

    sent = {"called": False}

    def fake_send(**kwargs):
        sent["called"] = True
        return True

    monkeypatch.setattr(
        "feedback_hub.pusher.webhook.send_markdown", fake_send,
    )

    rc = cli.main(["push", "--no-send"])
    assert rc == 0
    assert sent["called"] is False

    conn = db.connect()
    rows = conn.execute(
        "SELECT delivered_at, is_empty FROM push_log"
    ).fetchall()
    conn.close()
    assert len(rows) >= 1
    assert all(r["delivered_at"] is None for r in rows)


def test_push_real_marks_delivered_on_success(monkeypatch):
    conn = db.connect()
    db.init_schema(conn)
    _seed_one_p0_conv(conn)
    conn.close()

    monkeypatch.setenv("WECHAT_WEBHOOK_URL", "https://fake")
    monkeypatch.setattr(
        "feedback_hub.pusher.webhook.send_markdown",
        lambda **kw: True,
    )

    rc = cli.main(["push"])
    assert rc == 0

    conn = db.connect()
    rows = conn.execute(
        "SELECT delivered_at FROM push_log"
    ).fetchall()
    conn.close()
    assert all(r["delivered_at"] is not None for r in rows)


def test_push_real_keeps_delivered_null_on_failure(monkeypatch):
    conn = db.connect()
    db.init_schema(conn)
    _seed_one_p0_conv(conn)
    conn.close()

    monkeypatch.setenv("WECHAT_WEBHOOK_URL", "https://fake")
    monkeypatch.setattr(
        "feedback_hub.pusher.webhook.send_markdown",
        lambda **kw: False,
    )

    rc = cli.main(["push"])
    assert rc == 1  # 失败时返回非 0

    conn = db.connect()
    rows = conn.execute(
        "SELECT delivered_at FROM push_log"
    ).fetchall()
    conn.close()
    assert all(r["delivered_at"] is None for r in rows)


def test_push_no_webhook_url_aborts_real_mode(monkeypatch, capsys):
    """没设环境变量时，默认模式应报错退出。"""
    monkeypatch.delenv("WECHAT_WEBHOOK_URL", raising=False)
    rc = cli.main(["push"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "WECHAT_WEBHOOK_URL" in err


def test_push_dry_run_works_without_webhook_url(monkeypatch):
    """dry-run 不需要 webhook url。"""
    monkeypatch.delenv("WECHAT_WEBHOOK_URL", raising=False)
    conn = db.connect()
    db.init_schema(conn)
    conn.close()
    rc = cli.main(["push", "--dry-run"])
    assert rc == 0


def test_push_with_explicit_date(monkeypatch):
    conn = db.connect()
    db.init_schema(conn)
    _seed_one_p0_conv(conn)
    conn.close()

    rc = cli.main(["push", "--date", "2026-05-20", "--no-send"])
    assert rc == 0

    conn = db.connect()
    row = conn.execute(
        "SELECT push_date FROM push_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row["push_date"] == "2026-05-20"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest feedback_hub/tests/test_push_cli.py -v
```
Expected: FAIL —— `push` 子命令不存在

- [ ] **Step 3: cli.py 追加 cmd_push + 子命令注册**

在 `feedback_hub/cli.py` 中找到 `def cmd_serve(args)` 函数定义。在它**之前**追加 `cmd_push` 函数：

```python
def cmd_push(args) -> int:
    """生成 Top 5 候选 → 写 push_log → 推送企微（除非 --dry-run）。"""
    import time as _time
    from datetime import datetime as _dt

    from feedback_hub import config
    from feedback_hub.pusher import candidate, formatter, push_log, webhook

    ensure_dirs()

    # webhook url 校验（dry-run 与 no-send 不需要）
    if not args.dry_run and not args.no_send:
        if not config.get_webhook_url():
            print(
                "[push] error: 环境变量 WECHAT_WEBHOOK_URL 未设置；"
                "如仅查看候选请用 --dry-run",
                file=sys.stderr,
            )
            return 2

    # push_date：默认今天，可通过 --date 覆盖
    if args.date:
        push_date = args.date
        # now_ms 用 push_date 的 23:59:59 → 让 7 天窗口对齐"补推那天"
        dt = _dt.strptime(push_date, "%Y-%m-%d")
        now_ms = int((dt.timestamp() + 86399) * 1000)
    else:
        now_ms = int(_time.time() * 1000)
        push_date = _dt.fromtimestamp(now_ms / 1000).strftime("%Y-%m-%d")

    conn = db.connect()
    db.init_schema(conn)
    try:
        result = candidate.generate_candidates(conn, now_ms=now_ms)
        top_groups = result["top_groups"]
        markdown = formatter.format_message(
            push_date=push_date,
            scanned=result["scanned"],
            all_groups=result["all_groups_count"],
            top_groups=top_groups,
        )

        if args.dry_run:
            print(markdown)
            return 0

        created_at = int(_time.time())
        push_ids = push_log.save_top_groups(
            conn, push_date=push_date,
            top_groups=top_groups, created_at=created_at,
        )

        if args.no_send:
            print(markdown)
            return 0

        ok = webhook.send_markdown(
            webhook_url=config.get_webhook_url(),
            markdown=markdown,
        )
        if ok:
            push_log.mark_delivered(
                conn, push_ids, delivered_at=int(_time.time()),
            )
            print(f"[push] delivered {len(push_ids)} rows for {push_date}")
            return 0
        else:
            print(f"[push] webhook failed; push_log rows kept "
                  f"with delivered_at=NULL", file=sys.stderr)
            return 1
    finally:
        conn.close()
```

然后在 `build_parser()` 函数里，在 `return p` 之前追加子命令注册：

```python
    pu = sub.add_parser("push", help="生成 Top 5 候选并推送企微")
    pu.add_argument("--dry-run", action="store_true",
                    help="只打印消息体，不写 push_log 也不推送")
    pu.add_argument("--no-send", action="store_true",
                    help="写 push_log 但不调 webhook（用于自动化校验）")
    pu.add_argument("--date", default=None,
                    help="指定推送日期 YYYY-MM-DD，默认今天")
    pu.set_defaults(func=cmd_push)
```

最后更新 `feedback_hub/cli.py` 顶部 docstring 里的子命令列表（找到 `子命令：` 那段）：

```python
"""统一 CLI 入口：python -m feedback_hub.cli {pull,tag,serve,push}

子命令：
    pull   ：调 puller.pull 拉一段时间窗口
    tag    ：调 pipeline.run_tagging 跑一轮打标
    serve  ：用 uvicorn 启 FastAPI 服务
    push   ：生成 Top 5 Bug 候选并推送企微（spec 阶段 2）

时间窗口（同旧 pull_openapi.py，参考语义不复用代码）：
    --start-ts <unix秒>   --end-ts <unix秒>
    --start "YYYY-MM-DD HH:MM[:SS]"   --end "..."
    --last 30m | 2h | 1d
    --date 2026-05-18
    （默认）最近 24h
"""
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest feedback_hub/tests/test_push_cli.py -v
pytest feedback_hub/tests/test_cli.py -v   # 确认旧 cli 测试没坏
```
Expected: 全部 PASS

- [ ] **Step 5: 手工 dry-run 验证**

```bash
python -m feedback_hub.cli push --dry-run
```
Expected: 打印 Markdown 内容（如果生产 db 里有 P0/P1 Bug），或"今日无 Top Bug"。

- [ ] **Step 6: Commit**

```bash
git add feedback_hub/cli.py feedback_hub/tests/test_push_cli.py
git commit -m "feat(pusher): add cli push subcommand (dry-run/no-send/real)"
```

---

## Task 10: 30 天历史回灌脚本

**Files:**
- Create: `feedback_hub/scripts/backfill.sh`

- [ ] **Step 1: 创建 scripts 目录与脚本**

```bash
mkdir -p feedback_hub/scripts
```

创建 `feedback_hub/scripts/backfill.sh`：

```bash
#!/usr/bin/env bash
# 30 天历史回灌（spec §6.1 / §6.2）
# 用法：bash feedback_hub/scripts/backfill.sh
#
# 依赖：macOS 的 BSD `date -v` 语法。Linux 需另行适配。
# 幂等：feedback 主键冲突时跳过；message_label upsert；可重跑无害。

set -euo pipefail

# 切到仓库根目录（脚本位于 feedback_hub/scripts/，向上两级）
cd "$(dirname "$0")/../.."

echo "=== 30 天历史回灌开始 $(date '+%Y-%m-%d %H:%M:%S') ==="

FAILED_DAYS=()

for i in $(seq 30 -1 1); do
  DATE_START=$(date -v-${i}d +%Y-%m-%d)
  DATE_END=$(date -v-$((i-1))d +%Y-%m-%d)
  echo ">>> [$(date +%H:%M:%S)] 回灌 ${DATE_START} 00:00 ~ ${DATE_END} 00:00"
  if ! python -m feedback_hub.cli pull \
        --start "${DATE_START} 00:00:00" \
        --end "${DATE_END} 00:00:00"; then
    echo "!!! pull 失败 ${DATE_START}，记录后继续"
    FAILED_DAYS+=("${DATE_START}")
  fi
done

echo ""
echo "=== 拉取阶段完成；开始全量打标 ==="
python -m feedback_hub.cli tag --online

echo ""
echo "=== 回灌完成 $(date '+%Y-%m-%d %H:%M:%S') ==="
if [ ${#FAILED_DAYS[@]} -gt 0 ]; then
  echo "!!! 以下日期 pull 失败，需要人工补："
  printf '    %s\n' "${FAILED_DAYS[@]}"
  exit 1
fi
echo "全部 30 天均成功。"
```

- [ ] **Step 2: 加可执行权限**

```bash
chmod +x feedback_hub/scripts/backfill.sh
```

- [ ] **Step 3: 干跑校验（不实际执行回灌）**

```bash
bash -n feedback_hub/scripts/backfill.sh
```
Expected: 没有 syntax error

- [ ] **Step 4: 验证脚本能找到正确路径**

```bash
head -10 feedback_hub/scripts/backfill.sh | grep -q "cd" && \
  echo "ok: cd present"
```
Expected: 输出 `ok: cd present`

- [ ] **Step 5: Commit**

```bash
git add feedback_hub/scripts/backfill.sh
git commit -m "feat(pusher): add 30-day backfill shell script (macOS)"
```

- [ ] **Step 6: （手动）首次实跑**

> 注意：这一步会真实调用 OpenAPI 拉 30 天数据 + 跑全量打标，预计 30~60 分钟。请确认 `WINK_AGENT_KEY` 环境变量已设置后再跑。

```bash
export WINK_AGENT_KEY=...   # 如需要
bash feedback_hub/scripts/backfill.sh
```
Expected:
- 30 个 ">>> 回灌 YYYY-MM-DD" 行依次输出
- 打标统计 JSON 输出
- "全部 30 天均成功" 或失败日期列表

跑完后立即手动验证：

```bash
sqlite3 feedback_hub/data/feedback.db "
SELECT
  (SELECT COUNT(*) FROM feedback) AS feedback_n,
  (SELECT COUNT(*) FROM message_label) AS msg_label_n,
  (SELECT COUNT(*) FROM conversation_label) AS conv_label_n,
  (SELECT COUNT(*) FROM conversation_label WHERE L1='A.Bug') AS bug_convs;
"
python -m feedback_hub.cli push --dry-run
```

Expected: 三表均有数据；dry-run 打印出至少 1 条 Top Bug 候选（若有 P0/P1）或"今日无 Top Bug"。

---

## Self-Review

### 1. Spec coverage

逐项对照 spec 章节：

| Spec 章节 | 实现 task | ✅ |
|---|---|---|
| §1.2 交付 1（关键词分组） | T2 (kw_groups.yaml) | ✅ |
| §1.2 交付 2（签名计算 + 聚合打分） | T3 + T4 | ✅ |
| §1.2 交付 3（Top 5 候选生成） | T5 | ✅ |
| §1.2 交付 4（企微推送器） | T6 + T8 | ✅ |
| §1.2 交付 5（push_log 表） | T1 + T7 | ✅ |
| §1.2 交付 6（30 天回灌脚本） | T10 | ✅ |
| §1.2 交付 7（CLI 入口） | T9 | ✅ |
| §2.1 分组表 | T2 (kw_groups.yaml) | ✅ |
| §2.2 多组命中规则 | T3 (test_assign_group_*) | ✅ |
| §2.3 签名结构 | T3 (compute_signature) | ✅ |
| §2.4 配置外置 | T2 (config_loader) | ✅ |
| §2.5 方案 X（拼接 full_text） | T5 (CTE GROUP_CONCAT) + T3 测试 | ✅ |
| §3.2 SQL 拉取（CTE 三段） | T5 | ✅ |
| §3.3 聚合打分（含 representative_text） | T4 | ✅ |
| §3.4 权重默认值 | T2 (kw_groups.yaml scoring) | ✅ |
| §3.5 Top 5 选取 | T4 (pick_top5) | ✅ |
| §4.1 Markdown 格式 | T6 | ✅ |
| §4.2 webhook 配置 + 重试 | T8 | ✅ |
| §4.3 边界（空候选、失败处理） | T6 (空消息) + T7 (占位行) + T8 | ✅ |
| §5.1 push_log 表 | T1 | ✅ |
| §5.2 用途（落代表 fid） | T7 | ✅ |
| §5.3 schema 注册 | T1 | ✅ |
| §6.1 回灌实现 | T10 | ✅ |
| §6.2 关键设计点 | T10 (注释 + FAILED_DAYS) | ✅ |
| §11 验收：方案 X 召回 case | T3 + T5 都覆盖 | ✅ |
| §11 验收：代表文本来源 case | T5 (test_representative_text_is_top_confidence_message) | ✅ |
| §11 验收：0 候选 → is_empty=1 | T7 (test_save_empty_inserts_placeholder) | ✅ |
| §11 验收：webhook 失败 → delivered_at NULL | T9 (test_push_real_keeps_delivered_null_on_failure) | ✅ |

**覆盖率：100%**。

### 2. Placeholder scan

- 没有 "TBD" / "implement later" / "add appropriate error handling" 等模糊措辞
- 每个步骤都有完整代码、完整命令、明确预期
- 没有 "Similar to Task N" 这种偷懒引用
- 类型签名前后一致：`ConversationRow / CandidateGroup` 在 T3/T4/T5/T6/T7 全部用同一组字段名

### 3. Type consistency

- `ConversationRow` 字段：`conversation_id, L1, L2, severity, confidence, appversion, last_ts_ms, full_text, top_conf_text, top_conf_feedback_id` —— T4 定义后，T5 (`_row_to_conv`) 与所有测试 fixture (`_row()`) 使用一致
- `CandidateGroup` 字段：T4 定义包含 `dup_count / p0_count / cross_version / recent_24h / affected_versions / representative_text / representative_conv_id / representative_feedback_id` —— T6 (`_format_one`)、T7 (`_group_to_row`) 全部使用相同字段名
- `compute_signature` 三参数全 keyword：`full_text, L2, appversion` —— T3 定义后 T4 调用一致
- `db.insert_push_log` 字段顺序 = `PUSH_LOG_COLUMNS` 顺序 = `_group_to_row` 输出 dict keys —— 三处一致
- `webhook.send_markdown` 参数：`webhook_url, markdown, max_retries, timeout` —— T8 定义后 T9 调用一致

### 4. 风险敞口

| 风险 | 缓解 |
|---|---|
| `cli.cmd_push` 里 `push_date` 为 `--date` 覆盖时的 `now_ms` 处理可能错位 | 已用 `dt.timestamp() + 86399` 取该日 23:59:59，让 7 天窗口对齐 |
| `webhook.send_markdown` 用 `monkeypatch` 替换的是 `feedback_hub.pusher.webhook.requests.post`，需注意 import 路径 | 已在测试 monkeypatch 路径里明确写了 |
| `backfill.sh` 在 Linux 跑会失败（`date -v` 语法） | spec §6.2 明确接受；脚本注释里也写了 |

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-21-daily-bug-pusher.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
