# Feedback Label V2 Experiment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an offline v2 labeling experiment path that defines the new taxonomy, validates LLM JSON output, renders the v2 prompt, and writes experiment results to JSONL without touching production label tables.

**Architecture:** Add an isolated `feedback_hub/tagger/v2/` package. The package contains versioned taxonomy and prompt assets, a strict parser/validator, a prompt renderer, and an offline experiment runner. Existing `message_label`, `conversation_label`, `pipeline.py`, and legacy `L1/L2/severity` behavior remain unchanged.

**Tech Stack:** Python standard library, pytest, existing repository package layout, existing OpenAI-compatible caller can be injected later through a callable boundary.

---

## File Structure

- Create `feedback_hub/tagger/v2/__init__.py`: package marker and public version constant.
- Create `feedback_hub/tagger/v2/schema.py`: enum constants, field defaults, and validation helpers.
- Create `feedback_hub/tagger/v2/parser.py`: JSON extraction, normalization, validation, and fallback behavior for LLM replies.
- Create `feedback_hub/tagger/v2/prompt.md`: prompt template for the v2 taxonomy.
- Create `feedback_hub/tagger/v2/prompting.py`: load and render `prompt.md`.
- Create `feedback_hub/tagger/v2/taxonomy.yaml`: human-readable taxonomy source for review.
- Create `feedback_hub/tagger/v2/experiment.py`: offline JSONL experiment runner with injectable LLM caller and no database writes.
- Create `feedback_hub/tests/test_tagger_v2_parser.py`: parser and validation tests.
- Create `feedback_hub/tests/test_tagger_v2_prompting.py`: prompt rendering tests.
- Create `feedback_hub/tests/test_tagger_v2_experiment.py`: offline experiment runner tests.

No existing production tagger file should be modified in this first slice.

---

### Task 1: Add V2 Taxonomy Assets

**Files:**
- Create: `feedback_hub/tagger/v2/__init__.py`
- Create: `feedback_hub/tagger/v2/schema.py`
- Create: `feedback_hub/tagger/v2/taxonomy.yaml`
- Test: `feedback_hub/tests/test_tagger_v2_parser.py`

- [ ] **Step 1: Write the failing schema test**

Add this test file:

```python
"""Tests for feedback label v2 schema constants."""
from __future__ import annotations

from feedback_hub.tagger.v2 import schema


def test_schema_version_is_stable():
    assert schema.SCHEMA_VERSION == "feedback_label_v2"


def test_core_enums_include_low_priority_routing():
    assert "other_low_priority" in schema.PRODUCT_AREAS
    assert "account_sync" in schema.PRODUCT_AREAS
    assert "theme_skin" not in schema.PRODUCT_AREAS
    assert "account_login" not in schema.PRODUCT_AREAS


def test_invalid_feedback_types_skip_detailed_labels():
    assert schema.should_skip_detail("irrelevant_invalid") is True
    assert schema.should_skip_detail("bug_problem") is False
    assert schema.should_skip_detail("feature_request") is False
    assert schema.should_skip_detail("improvement_request") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_tagger_v2_parser.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'feedback_hub.tagger.v2'`.

- [ ] **Step 3: Add package and schema constants**

Create `feedback_hub/tagger/v2/__init__.py`:

```python
"""Feedback label v2 experiment package."""
from __future__ import annotations

SCHEMA_VERSION = "feedback_label_v2"
```

Create `feedback_hub/tagger/v2/schema.py`:

```python
"""Schema constants and helpers for feedback label v2."""
from __future__ import annotations

SCHEMA_VERSION = "feedback_label_v2"

FEEDBACK_TYPES = {
    "bug_problem",
    "feature_request",
    "improvement_request",
    "question_help",
    "sentiment_only",
    "irrelevant_invalid",
    "mixed",
}

DETAIL_REQUIRED_TYPES = {
    "bug_problem",
    "feature_request",
    "improvement_request",
}

PRODUCT_AREAS = {
    "input_core",
    "voice_input",
    "emoji_expression",
    "ai_generation",
    "clipboard_sync",
    "keyboard_ui",
    "dictionary_phrases",
    "permissions_privacy",
    "performance_stability",
    "install_update",
    "cross_app_compatibility",
    "account_sync",
    "other_low_priority",
    "other_unknown",
}

ISSUE_PATTERNS = {
    "unavailable_or_broken",
    "incorrect_or_poor_result",
    "missing_or_unsupported",
    "hard_to_use_or_trigger",
    "performance_problem",
    "layout_or_display_problem",
    "compatibility_problem",
    "data_or_sync_problem",
    "other_unknown",
}

EVIDENCE_SIGNALS = {
    "has_actual_behavior",
    "has_expected_behavior",
    "has_context",
    "has_repro_steps",
    "has_scenario",
    "has_comparison",
    "has_workaround",
    "vague",
}

VALUE_SIGNALS = {
    "clear_actionable",
    "strong_pain",
    "workflow_blocker",
    "new_scenario",
    "regression_suspected",
    "competitor_mentioned",
    "retention_risk",
    "positive_signal",
}

OBSERVABLE_IMPACTS = {
    "blocked",
    "degraded",
    "friction",
    "preference",
    "unknown",
}

ACTIONABILITY_VALUES = {
    "actionable",
    "external_constraint",
    "product_policy",
    "insufficient_info",
    "unknown",
}


def should_skip_detail(feedback_type: str) -> bool:
    return feedback_type == "irrelevant_invalid"
```

Create `feedback_hub/tagger/v2/taxonomy.yaml` from the spec with the same enum values. Keep it human-readable; runtime validation uses `schema.py` in this first slice to avoid adding YAML parsing as a dependency.

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_tagger_v2_parser.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add feedback_hub/tagger/v2/__init__.py feedback_hub/tagger/v2/schema.py feedback_hub/tagger/v2/taxonomy.yaml feedback_hub/tests/test_tagger_v2_parser.py
git commit -m "feat: add feedback label v2 taxonomy"
```

---

### Task 2: Add V2 LLM Reply Parser

**Files:**
- Create: `feedback_hub/tagger/v2/parser.py`
- Modify: `feedback_hub/tests/test_tagger_v2_parser.py`

- [ ] **Step 1: Extend tests for JSON parsing and conditional fields**

Append these tests to `feedback_hub/tests/test_tagger_v2_parser.py`:

```python
from feedback_hub.tagger.v2.parser import parse_label_reply


def test_parse_valid_v2_label_from_fenced_json():
    reply = """```json
{
  "schema_version": "feedback_label_v2",
  "feedback_type": "bug_problem",
  "primary_feedback_type": "bug_problem",
  "product_area": ["voice_input"],
  "issue_pattern": ["incorrect_or_poor_result"],
  "evidence_signal": ["has_actual_behavior", "has_context"],
  "value_signal": ["clear_actionable"],
  "observable_impact": "degraded",
  "actionability": "unknown",
  "evidence_span": "语音识别经常错",
  "reason": "语音识别结果不准确",
  "confidence": 0.82
}
```"""
    result = parse_label_reply(reply)
    assert result["schema_version"] == "feedback_label_v2"
    assert result["feedback_type"] == "bug_problem"
    assert result["product_area"] == ["voice_input"]
    assert result["issue_pattern"] == ["incorrect_or_poor_result"]
    assert result["confidence"] == 0.82
    assert result["parse_error"] is None


def test_irrelevant_feedback_clears_detailed_fields():
    result = parse_label_reply(
        '{"feedback_type":"irrelevant_invalid","product_area":["voice_input"],'
        '"issue_pattern":["incorrect_or_poor_result"],"confidence":0.9}'
    )
    assert result["feedback_type"] == "irrelevant_invalid"
    assert result["product_area"] is None
    assert result["issue_pattern"] is None
    assert result["skip_reason"] == "irrelevant_invalid"


def test_required_detail_missing_becomes_unknown():
    result = parse_label_reply(
        '{"feedback_type":"bug_problem","product_area":[],"issue_pattern":[],"confidence":0.7}'
    )
    assert result["product_area"] == ["other_unknown"]
    assert result["issue_pattern"] == ["other_unknown"]


def test_invalid_enum_values_are_filtered():
    result = parse_label_reply(
        '{"feedback_type":"bug_problem","product_area":["theme_skin","voice_input"],'
        '"issue_pattern":["made_up","performance_problem"],"confidence":2}'
    )
    assert result["product_area"] == ["voice_input"]
    assert result["issue_pattern"] == ["performance_problem"]
    assert result["confidence"] == 1.0


def test_no_json_returns_parse_error_fallback():
    result = parse_label_reply("无法处理")
    assert result["feedback_type"] == "irrelevant_invalid"
    assert result["parse_error"] == "no_json_found"
    assert result["confidence"] == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_tagger_v2_parser.py -v
```

Expected: FAIL with `ModuleNotFoundError` or `ImportError` for `feedback_hub.tagger.v2.parser`.

- [ ] **Step 3: Implement parser**

Create `feedback_hub/tagger/v2/parser.py`:

```python
"""Parse and validate feedback label v2 LLM replies."""
from __future__ import annotations

import json
import re
from typing import Any

from feedback_hub.tagger.v2 import schema

_FENCE_JSON_RE = re.compile(r"```json\s*(.+?)\s*```", re.DOTALL | re.IGNORECASE)
_FENCE_ANY_RE = re.compile(r"```\s*(.+?)\s*```", re.DOTALL)
_BRACE_RE = re.compile(r"\{[\s\S]*\}")


def _extract_json_str(reply: str) -> str | None:
    if not reply:
        return None
    match = _FENCE_JSON_RE.search(reply)
    if match:
        return match.group(1).strip()
    match = _FENCE_ANY_RE.search(reply)
    if match:
        candidate = match.group(1).strip()
        if candidate.startswith("{"):
            return candidate
    match = _BRACE_RE.search(reply)
    if match:
        return match.group(0).strip()
    return None


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [x for x in value if isinstance(x, str)]
    if isinstance(value, str):
        return [value]
    return []


def _filter(values: Any, allowed: set[str]) -> list[str]:
    return [x for x in _as_list(values) if x in allowed]


def _clamp_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except Exception:
        confidence = 0.0
    return max(0.0, min(1.0, confidence))


def _text(value: Any, max_len: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return value.strip()[:max_len]


def _fallback(parse_error: str) -> dict[str, Any]:
    return {
        "schema_version": schema.SCHEMA_VERSION,
        "feedback_type": "irrelevant_invalid",
        "primary_feedback_type": "irrelevant_invalid",
        "product_area": None,
        "issue_pattern": None,
        "evidence_signal": ["vague"],
        "value_signal": [],
        "observable_impact": "unknown",
        "actionability": "insufficient_info",
        "skip_reason": "parse_error",
        "evidence_span": "",
        "reason": "parse_error",
        "confidence": 0.0,
        "parse_error": parse_error,
    }


def parse_label_reply(reply: str) -> dict[str, Any]:
    raw = _extract_json_str(reply or "")
    if raw is None:
        return _fallback("no_json_found")
    try:
        obj = json.loads(raw)
    except Exception as exc:
        return _fallback(type(exc).__name__)
    if not isinstance(obj, dict):
        return _fallback("not_object")

    feedback_type = obj.get("feedback_type")
    if feedback_type not in schema.FEEDBACK_TYPES:
        feedback_type = "irrelevant_invalid"

    primary = obj.get("primary_feedback_type")
    if primary not in schema.FEEDBACK_TYPES:
        primary = feedback_type

    evidence_signal = _filter(obj.get("evidence_signal"), schema.EVIDENCE_SIGNALS)
    if not evidence_signal:
        evidence_signal = ["vague"]

    value_signal = _filter(obj.get("value_signal"), schema.VALUE_SIGNALS)

    observable_impact = obj.get("observable_impact")
    if observable_impact not in schema.OBSERVABLE_IMPACTS:
        observable_impact = "unknown"

    actionability = obj.get("actionability")
    if actionability not in schema.ACTIONABILITY_VALUES:
        actionability = "unknown"

    product_area = _filter(obj.get("product_area"), schema.PRODUCT_AREAS)
    issue_pattern = _filter(obj.get("issue_pattern"), schema.ISSUE_PATTERNS)
    skip_reason = None

    if schema.should_skip_detail(feedback_type):
        product_area_out: list[str] | None = None
        issue_pattern_out: list[str] | None = None
        skip_reason = "irrelevant_invalid"
    else:
        product_area_out = product_area
        issue_pattern_out = issue_pattern
        if feedback_type in schema.DETAIL_REQUIRED_TYPES:
            if not product_area_out:
                product_area_out = ["other_unknown"]
            if not issue_pattern_out:
                issue_pattern_out = ["other_unknown"]

    result = {
        "schema_version": schema.SCHEMA_VERSION,
        "feedback_type": feedback_type,
        "primary_feedback_type": primary,
        "product_area": product_area_out,
        "issue_pattern": issue_pattern_out,
        "evidence_signal": evidence_signal,
        "value_signal": value_signal,
        "observable_impact": observable_impact,
        "actionability": actionability,
        "evidence_span": _text(obj.get("evidence_span"), 120),
        "reason": _text(obj.get("reason"), 120) or "llm",
        "confidence": _clamp_confidence(obj.get("confidence", 0.0)),
        "parse_error": None,
    }
    if skip_reason:
        result["skip_reason"] = skip_reason
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_tagger_v2_parser.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add feedback_hub/tagger/v2/parser.py feedback_hub/tests/test_tagger_v2_parser.py
git commit -m "feat: parse feedback label v2 replies"
```

---

### Task 3: Add Prompt Template and Renderer

**Files:**
- Create: `feedback_hub/tagger/v2/prompt.md`
- Create: `feedback_hub/tagger/v2/prompting.py`
- Test: `feedback_hub/tests/test_tagger_v2_prompting.py`

- [ ] **Step 1: Write failing prompt tests**

Create `feedback_hub/tests/test_tagger_v2_prompting.py`:

```python
"""Tests for feedback label v2 prompt rendering."""
from __future__ import annotations

from feedback_hub.tagger.v2.prompting import build_prompt


def test_build_prompt_includes_feedback_text_and_schema():
    prompt = build_prompt("语音识别经常错", metadata={"platform": "iOS", "appversion": "3.2.1"})
    assert "语音识别经常错" in prompt
    assert "feedback_label_v2" in prompt
    assert "iOS" in prompt
    assert "product_area" in prompt
    assert "issue_pattern" in prompt


def test_build_prompt_escapes_braces_in_feedback_text():
    prompt = build_prompt("输入 {test} 会异常", metadata={})
    assert "输入 {test} 会异常" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_tagger_v2_prompting.py -v
```

Expected: FAIL with `ModuleNotFoundError` for `feedback_hub.tagger.v2.prompting`.

- [ ] **Step 3: Add prompt asset and renderer**

Create `feedback_hub/tagger/v2/prompt.md` with a JSON-only instruction that includes:

```markdown
# Feedback Label V2 Prompt

You are labeling user feedback for a feedback mining report.

Return JSON only. Do not explain outside JSON.

Schema version: feedback_label_v2

Core rule:
- First decide `feedback_type`.
- Only label `product_area` and `issue_pattern` when the feedback is analytically useful.
- If `feedback_type` is `irrelevant_invalid`, set `product_area` and `issue_pattern` to null.
- Do not infer root cause, owner, new issue, or rising trend.
- `observable_impact` and `actionability` describe only what is visible in the feedback text.

Feedback text:
{{TEXT}}

Metadata:
{{METADATA}}

Return this JSON shape:
{
  "schema_version": "feedback_label_v2",
  "feedback_type": "bug_problem | feature_request | improvement_request | question_help | sentiment_only | irrelevant_invalid | mixed",
  "primary_feedback_type": "bug_problem | feature_request | improvement_request | question_help | sentiment_only | irrelevant_invalid",
  "product_area": ["input_core | voice_input | emoji_expression | ai_generation | clipboard_sync | keyboard_ui | dictionary_phrases | permissions_privacy | performance_stability | install_update | cross_app_compatibility | account_sync | other_low_priority | other_unknown"] | null,
  "issue_pattern": ["unavailable_or_broken | incorrect_or_poor_result | missing_or_unsupported | hard_to_use_or_trigger | performance_problem | layout_or_display_problem | compatibility_problem | data_or_sync_problem | other_unknown"] | null,
  "evidence_signal": ["has_actual_behavior | has_expected_behavior | has_context | has_repro_steps | has_scenario | has_comparison | has_workaround | vague"],
  "value_signal": ["clear_actionable | strong_pain | workflow_blocker | new_scenario | regression_suspected | competitor_mentioned | retention_risk | positive_signal"],
  "observable_impact": "blocked | degraded | friction | preference | unknown",
  "actionability": "actionable | external_constraint | product_policy | insufficient_info | unknown",
  "evidence_span": "short original text span",
  "reason": "short reason",
  "confidence": 0.0
}
```

Create `feedback_hub/tagger/v2/prompting.py`:

```python
"""Prompt rendering for feedback label v2 experiments."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROMPT_PATH = Path(__file__).with_name("prompt.md")


def build_prompt(text: str, *, metadata: dict[str, Any] | None = None) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    metadata_json = json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)
    return template.replace("{{TEXT}}", text or "").replace("{{METADATA}}", metadata_json)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_tagger_v2_prompting.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add feedback_hub/tagger/v2/prompt.md feedback_hub/tagger/v2/prompting.py feedback_hub/tests/test_tagger_v2_prompting.py
git commit -m "feat: add feedback label v2 prompt"
```

---

### Task 4: Add Offline JSONL Experiment Runner

**Files:**
- Create: `feedback_hub/tagger/v2/experiment.py`
- Test: `feedback_hub/tests/test_tagger_v2_experiment.py`

- [ ] **Step 1: Write failing experiment tests**

Create `feedback_hub/tests/test_tagger_v2_experiment.py`:

```python
"""Tests for offline feedback label v2 experiments."""
from __future__ import annotations

import json

from feedback_hub.tagger.v2.experiment import run_jsonl_experiment


def test_run_jsonl_experiment_writes_parsed_results(tmp_path):
    input_path = tmp_path / "input.jsonl"
    output_path = tmp_path / "output.jsonl"
    input_path.write_text(
        json.dumps({"feedback_id": "f1", "text": "语音识别经常错", "platform": "iOS"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    def fake_llm(prompt: str) -> str:
        assert "语音识别经常错" in prompt
        return json.dumps({
            "schema_version": "feedback_label_v2",
            "feedback_type": "bug_problem",
            "primary_feedback_type": "bug_problem",
            "product_area": ["voice_input"],
            "issue_pattern": ["incorrect_or_poor_result"],
            "evidence_signal": ["has_actual_behavior"],
            "value_signal": ["clear_actionable"],
            "observable_impact": "degraded",
            "actionability": "unknown",
            "evidence_span": "语音识别经常错",
            "reason": "语音结果错误",
            "confidence": 0.8,
        }, ensure_ascii=False)

    stats = run_jsonl_experiment(input_path, output_path, llm_call=fake_llm, limit=None)

    assert stats == {"total": 1, "labeled": 1, "failed": 0}
    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["feedback_id"] == "f1"
    assert rows[0]["label"]["product_area"] == ["voice_input"]
    assert rows[0]["raw_reply"]


def test_run_jsonl_experiment_respects_limit(tmp_path):
    input_path = tmp_path / "input.jsonl"
    output_path = tmp_path / "output.jsonl"
    input_path.write_text(
        "\n".join([
            json.dumps({"feedback_id": "f1", "text": "a"}, ensure_ascii=False),
            json.dumps({"feedback_id": "f2", "text": "b"}, ensure_ascii=False),
        ]) + "\n",
        encoding="utf-8",
    )

    def fake_llm(prompt: str) -> str:
        return '{"feedback_type":"irrelevant_invalid","confidence":0.9}'

    stats = run_jsonl_experiment(input_path, output_path, llm_call=fake_llm, limit=1)

    assert stats["total"] == 1
    assert len(output_path.read_text(encoding="utf-8").splitlines()) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_tagger_v2_experiment.py -v
```

Expected: FAIL with `ModuleNotFoundError` for `feedback_hub.tagger.v2.experiment`.

- [ ] **Step 3: Implement experiment runner**

Create `feedback_hub/tagger/v2/experiment.py`:

```python
"""Offline JSONL experiment runner for feedback label v2."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from feedback_hub.tagger.v2.parser import parse_label_reply
from feedback_hub.tagger.v2.prompting import build_prompt

LLMCall = Callable[[str], str]


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if not isinstance(obj, dict):
                raise ValueError(f"line {line_no} is not a JSON object")
            yield obj


def _metadata(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item[key]
        for key in ("platform", "appversion", "channel", "device", "source", "ts_ms")
        if key in item and item[key] is not None
    }


def run_jsonl_experiment(
    input_path: str | Path,
    output_path: str | Path,
    *,
    llm_call: LLMCall,
    limit: int | None = None,
) -> dict[str, int]:
    input_path = Path(input_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    stats = {"total": 0, "labeled": 0, "failed": 0}
    with output_path.open("w", encoding="utf-8") as out:
        for item in _iter_jsonl(input_path):
            if limit is not None and stats["total"] >= limit:
                break
            stats["total"] += 1
            text = str(item.get("text") or "")
            prompt = build_prompt(text, metadata=_metadata(item))
            try:
                raw_reply = llm_call(prompt)
                label = parse_label_reply(raw_reply)
                stats["labeled"] += 1
                error = None
            except Exception as exc:
                raw_reply = ""
                label = parse_label_reply("")
                stats["failed"] += 1
                error = f"{type(exc).__name__}: {exc}"
            row = {
                "feedback_id": item.get("feedback_id"),
                "text": text,
                "metadata": _metadata(item),
                "label": label,
                "raw_reply": raw_reply,
                "error": error,
            }
            out.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run offline feedback label v2 JSONL experiment.")
    parser.add_argument("--input", required=True, help="Input JSONL with feedback_id and text fields.")
    parser.add_argument("--output", required=True, help="Output JSONL path.")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)
    raise SystemExit("CLI LLM provider wiring is intentionally not enabled in the first slice; use run_jsonl_experiment with an injected llm_call.")


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_tagger_v2_experiment.py -v
```

Expected: PASS.

- [ ] **Step 5: Run full v2 test subset**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_tagger_v2_parser.py feedback_hub/tests/test_tagger_v2_prompting.py feedback_hub/tests/test_tagger_v2_experiment.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add feedback_hub/tagger/v2/experiment.py feedback_hub/tests/test_tagger_v2_experiment.py
git commit -m "feat: add feedback label v2 experiment runner"
```

---

## Self-Review

Spec coverage:

- Core labels are represented in `schema.py`.
- Conditional labeling is covered by `should_skip_detail` and parser behavior.
- Low-priority theme/account treatment is covered by allowed product areas.
- Experimental impact/actionability fields are parsed but not used for core routing.
- Offline-only execution is covered by `experiment.py`, which writes JSONL and does not write to the database.

Placeholder scan:

- No task relies on undefined paths.
- No task asks for unspecified tests.
- CLI provider wiring is explicitly out of scope for this first slice and fails closed.

Type consistency:

- `parse_label_reply` returns dictionaries consumed by `run_jsonl_experiment`.
- `build_prompt(text, metadata=...)` is used consistently by tests and the experiment runner.
- `schema.SCHEMA_VERSION` is the single source of truth for the output schema version.

