# Topic JSONL Reader Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every production JSONL reader in topic mining treat only LF (`\n`) as a record delimiter, while preserving each caller's existing error and recovery behavior.

**Architecture:** Add a focused `jsonl_io.py` module with one LF-only text-record decoder and one strict JSON-object loader. Service, Export, and review API use the strict loader and retain their domain error mapping; classifier recovery code uses the text-record decoder so malformed-record tolerance remains unchanged.

**Tech Stack:** Python 3.11, standard-library `json`, pytest, FastAPI TestClient, openpyxl

## Global Constraints

- Preserve source feedback text byte-for-byte after UTF-8 decoding; do not sanitize U+0085, U+2028, or U+2029.
- Treat only LF (`\n`) as the JSONL record delimiter.
- Preserve `invalid_artifact` and `invalid review queue artifact` public error contracts.
- Preserve classifier checkpoint/audit tolerance for individually malformed records.
- Do not modify non-topic packages or human-authored text/configuration readers.
- Do not stage or modify the user-owned untracked root `WORKSPACE_GUIDE.md`.

---

### Task 1: Shared LF-only JSONL parser

**Files:**
- Create: `feedback_hub/topic_mining/jsonl_io.py`
- Create: `feedback_hub/tests/test_topic_mining_jsonl_io.py`

**Interfaces:**
- Consumes: UTF-8 JSONL as `bytes`.
- Produces: `jsonl_text_records(raw: bytes) -> tuple[str, ...]` and `load_jsonl_objects(raw: bytes) -> list[dict[str, Any]]`.

- [ ] **Step 1: Write failing parser tests**

```python
import json

import pytest

from feedback_hub.topic_mining.jsonl_io import (
    jsonl_text_records,
    load_jsonl_objects,
)


@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
def test_jsonl_parser_preserves_unicode_line_separator_inside_string(separator):
    expected = {"item_id": "f1", "text": f"第一段{separator}第二段"}
    raw = (json.dumps(expected, ensure_ascii=False) + "\n").encode("utf-8")

    assert load_jsonl_objects(raw) == [expected]


def test_jsonl_text_records_split_only_on_lf():
    raw = b'{"value":"a\xe2\x80\xa8b"}\n{"value":"c"}\n'

    assert jsonl_text_records(raw) == (
        '{"value":"a\u2028b"}',
        '{"value":"c"}',
    )


@pytest.mark.parametrize("raw", [b"{broken\n", b"[]\n"])
def test_strict_jsonl_object_loader_rejects_invalid_records(raw):
    with pytest.raises((json.JSONDecodeError, ValueError)):
        load_jsonl_objects(raw)
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_topic_mining_jsonl_io.py -q
```

Expected: collection fails with `ModuleNotFoundError: feedback_hub.topic_mining.jsonl_io`.

- [ ] **Step 3: Implement the shared parser**

```python
"""Shared LF-delimited JSONL parsing for topic-mining artifacts."""
from __future__ import annotations

import json
from typing import Any


def jsonl_text_records(raw: bytes) -> tuple[str, ...]:
    text = raw.decode("utf-8")
    return tuple(record for record in text.split("\n") if record.strip())


def load_jsonl_objects(raw: bytes) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for record in jsonl_text_records(raw):
        value = json.loads(record)
        if not isinstance(value, dict):
            raise ValueError("JSONL records must be objects")
        values.append(value)
    return values
```

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python3 -m pytest feedback_hub/tests/test_topic_mining_jsonl_io.py -q`

Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add feedback_hub/topic_mining/jsonl_io.py feedback_hub/tests/test_topic_mining_jsonl_io.py
git commit -m "feat: add shared topic JSONL parser"
```

---

### Task 2: Service and Export strict artifact readers

**Files:**
- Modify: `feedback_hub/topic_mining/service.py:1530-1570,1685-1710`
- Modify: `feedback_hub/topic_mining/export.py:25-115`
- Modify: `feedback_hub/tests/test_topic_mining_service.py:800-840`
- Modify: `feedback_hub/tests/test_topic_mining_export.py`

**Interfaces:**
- Consumes: `load_jsonl_objects(raw: bytes)` from Task 1.
- Produces: unchanged Service/Export return values and `RunVerificationError("invalid_artifact")` mapping.

- [ ] **Step 1: Extend Service separator coverage**

Change both existing separator parameter lists to include U+0085:

```python
@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
```

- [ ] **Step 2: Add an Export production-regression test**

Add a test that creates a verified run with one final result and two recall candidates. Put `"第一段\u2028第二段"` in the second, unmatched recall item's `item.text`, include both `final_reviewed.jsonl` and `recall_candidates.jsonl` in `_persist_verified_manifest`, then export both formats from separately initialized runs:

```python
@pytest.mark.parametrize("export_format", ["jsonl", "xlsx"])
def test_export_preserves_unicode_separator_in_recall_pool(tmp_path, export_format):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(_spec(), CUTOFF_MS)
    artifact_dir = Path(run["artifact_dir"])
    final = artifact_dir / "final_reviewed.jsonl"
    final.write_text(
        json.dumps(_row(run["run_id"]), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    recall_rows = []
    for item_id, text in (
        ("f-1", "游戏全屏工具栏一直显示"),
        ("f-2", "第一段\u2028第二段"),
    ):
        item = dict(_row(run["run_id"], item_id)["source_item"])
        item["text"] = text
        recall_rows.append({
            "item_id": item_id, "item": item, "channels": ["bm25"],
            "fused_score": 1.0, "fused_rank": len(recall_rows) + 1,
            "channel_ranks": {"bm25": len(recall_rows) + 1},
            "raw_scores": {"bm25": 1.0}, "query_ids": ["q1"],
            "negative_query_hits": [],
        })
    recalls = artifact_dir / "recall_candidates.jsonl"
    recalls.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in recall_rows),
        encoding="utf-8",
    )
    _persist_verified_manifest(store, run, final, recalls)

    exported = export_topic_run(run["run_id"], export_format, store=store)

    assert exported.is_file()
    persisted = load_jsonl_objects(recalls.read_bytes())
    assert persisted[1]["item"]["text"] == "第一段\u2028第二段"
```

- [ ] **Step 3: Run focused tests and verify RED**

Run:

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_mining_service.py -k unicode_separator \
  feedback_hub/tests/test_topic_mining_export.py -k unicode_separator -q
```

Expected: Service tests pass for existing fixed paths; both Export parameter cases fail with `RunVerificationError: invalid_artifact`.

- [ ] **Step 4: Replace Service and Export parsing with the shared loader**

Import `load_jsonl_objects` in both modules. Replace Service's manual decode/split/`json.loads` loops with `load_jsonl_objects(raw)` inside the existing exception mapping. Replace both Export comprehensions with:

```python
rows = load_jsonl_objects(read_verified_artifact_bytes(manifest, final_path))
```

and:

```python
recalls = [
    _recall_from_dict(row)
    for row in load_jsonl_objects(
        read_verified_artifact_bytes(manifest, recall_path)
    )
]
```

Catch `UnicodeDecodeError`, `json.JSONDecodeError`, `TypeError`, and `ValueError` as currently appropriate, continuing to raise `RunVerificationError("invalid_artifact")`.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_mining_jsonl_io.py \
  feedback_hub/tests/test_topic_mining_service.py -k 'unicode_separator or malformed_json' \
  feedback_hub/tests/test_topic_mining_export.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add feedback_hub/topic_mining/service.py feedback_hub/topic_mining/export.py \
  feedback_hub/tests/test_topic_mining_service.py feedback_hub/tests/test_topic_mining_export.py
git commit -m "fix: use shared JSONL parser for topic artifacts"
```

---

### Task 3: Review-queue API readers

**Files:**
- Modify: `feedback_hub/topic_mining/api.py:517-553`
- Modify: `feedback_hub/tests/test_topic_mining_api.py`

**Interfaces:**
- Consumes: `load_jsonl_objects(raw: bytes)` from Task 1.
- Produces: unchanged `_read_jsonl(path)` and `_parse_jsonl_bytes(raw)` results and `ValueError("invalid review queue artifact")` mapping.

- [ ] **Step 1: Write failing API reader tests**

```python
@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
def test_review_queue_jsonl_readers_preserve_unicode_separators(tmp_path, separator):
    from feedback_hub.topic_mining.api import _parse_jsonl_bytes, _read_jsonl

    expected = {"item_id": "f1", "text": f"第一段{separator}第二段"}
    raw = (json.dumps(expected, ensure_ascii=False) + "\n").encode("utf-8")
    path = tmp_path / "review_queue.jsonl"
    path.write_bytes(raw)

    assert _read_jsonl(path) == [expected]
    assert _parse_jsonl_bytes(raw) == [expected]
```

- [ ] **Step 2: Run test and verify RED**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_topic_mining_api.py \
  -k review_queue_jsonl_readers_preserve_unicode_separators -q
```

Expected: all three parameter cases fail with `invalid review queue artifact`.

- [ ] **Step 3: Adopt the shared strict loader**

Make `_read_jsonl(path)` read bytes and delegate to `_parse_jsonl_bytes`. Make `_read_jsonl_bytes(raw)` return `load_jsonl_objects(raw)`. Keep `_parse_jsonl_bytes` as the domain-error wrapper:

```python
def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError("invalid review queue artifact") from exc
    return _parse_jsonl_bytes(raw)


def _read_jsonl_bytes(raw: bytes) -> list[dict[str, Any]]:
    return load_jsonl_objects(raw)
```

- [ ] **Step 4: Run API tests and verify GREEN**

Run: `python3 -m pytest feedback_hub/tests/test_topic_mining_api.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add feedback_hub/topic_mining/api.py feedback_hub/tests/test_topic_mining_api.py
git commit -m "fix: unify review queue JSONL parsing"
```

---

### Task 4: Classifier checkpoint and audit recovery readers

**Files:**
- Modify: `feedback_hub/topic_mining/classifier.py:430-525`
- Modify: `feedback_hub/tests/test_topic_mining_classifier.py`

**Interfaces:**
- Consumes: `jsonl_text_records(raw: bytes)` from Task 1.
- Produces: unchanged checkpoint retention, audit-generation discovery, and append behavior with LF-only record boundaries.

- [ ] **Step 1: Write failing classifier recovery tests**

Add focused tests for the three JSONL consumers:

```python
def test_checkpoint_and_audit_readers_preserve_unicode_separator(tmp_path):
    from feedback_hub.topic_mining.classifier import (
        _next_audit_generation,
        _retain_successful_checkpoints,
        _write_audit,
    )

    batches = tmp_path / "classification_batches.jsonl"
    checkpoint = batches.with_suffix(batches.suffix + ".checkpoint.jsonl")
    checkpoint_row = {"ok": True, "raw_reply": "第一段\u2028第二段"}
    checkpoint.write_text(
        json.dumps(checkpoint_row, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _retain_successful_checkpoints(batches)
    assert load_jsonl_objects(checkpoint.read_bytes()) == [checkpoint_row]

    audit = tmp_path / "classification_audit.jsonl"
    existing = {
        "audit_id": "g1:a", "audit_generation": 1,
        "raw_replies": ["第一段\u2028第二段"],
    }
    audit.write_text(
        json.dumps(existing, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    assert _next_audit_generation(audit, tmp_path / "partials") == 2
    _write_audit(audit, [], append=True, secrets=())
    assert load_jsonl_objects(audit.read_bytes()) == [existing]
```

- [ ] **Step 2: Run test and verify RED**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_topic_mining_classifier.py \
  -k checkpoint_and_audit_readers_preserve_unicode_separator -q
```

Expected: the checkpoint row is split and discarded, or audit generation/history is incorrect.

- [ ] **Step 3: Replace classifier `splitlines()` calls**

Import `jsonl_text_records`. In `_retain_successful_checkpoints`, `_next_audit_generation`, and `_write_audit`, replace each `path.read_text(...).splitlines()` loop with:

```python
for line in jsonl_text_records(path.read_bytes()):
```

Keep the existing per-line `json.JSONDecodeError` handling so malformed checkpoint/audit rows remain skippable exactly as before.

- [ ] **Step 4: Run classifier tests and verify GREEN**

Run: `python3 -m pytest feedback_hub/tests/test_topic_mining_classifier.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add feedback_hub/topic_mining/classifier.py feedback_hub/tests/test_topic_mining_classifier.py
git commit -m "fix: unify classifier JSONL recovery parsing"
```

---

### Task 5: Full regression and reader audit

**Files:**
- Modify only if a failing regression exposes an in-scope omission.

**Interfaces:**
- Consumes: all code and tests from Tasks 1-4.
- Produces: verified topic-mining suite and confirmation that all topic JSONL production readers use the shared LF-only boundary.

- [ ] **Step 1: Confirm no production JSONL reader still uses `splitlines()`**

Run:

```bash
rg -n "splitlines\(\)" feedback_hub/topic_mining
```

Expected: no JSONL production reader matches. Any remaining match must be demonstrated to parse a non-JSONL text format or replaced with the shared helper and covered by a focused test.

- [ ] **Step 2: Run the complete topic-mining suite**

Run:

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_mining_jsonl_io.py \
  feedback_hub/tests/test_topic_mining_service.py \
  feedback_hub/tests/test_topic_mining_export.py \
  feedback_hub/tests/test_topic_mining_api.py \
  feedback_hub/tests/test_topic_mining_classifier.py \
  feedback_hub/tests/test_topic_mining_end_to_end.py -q
```

Expected: all tests pass with no warnings introduced by this change.

- [ ] **Step 3: Commit any in-scope regression correction**

If Step 1 or Step 2 required an additional in-scope code change, add its focused failing test first, verify RED/GREEN, and commit only those files. If no correction was needed, do not create an empty commit.
