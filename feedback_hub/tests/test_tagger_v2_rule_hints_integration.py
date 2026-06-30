"""Integration tests for feedback label v2 rule hints in experiments."""
from __future__ import annotations

import json

from feedback_hub.tagger.v2.experiment import run_jsonl_experiment
from feedback_hub.tagger.v2.prompting import build_prompt


def test_build_prompt_includes_rule_hints():
    prompt = build_prompt(
        "语音输入经常识别错",
        metadata={"platform": "iOS"},
        rule_hints={"maybe_product_area": ["voice_input"], "skip_llm": False},
    )
    assert "Rule hints" in prompt
    assert "voice_input" in prompt
    assert "Hints are not final labels" in prompt


def test_experiment_skips_llm_for_high_confidence_invalid(tmp_path):
    input_path = tmp_path / "input.jsonl"
    output_path = tmp_path / "output.jsonl"
    input_path.write_text(
        json.dumps({"feedback_id": "f1", "text": "测试"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    calls = []

    def fake_llm(prompt: str) -> str:
        calls.append(prompt)
        return "{}"

    stats = run_jsonl_experiment(input_path, output_path, llm_call=fake_llm)

    assert stats == {"total": 1, "labeled": 1, "failed": 0, "skipped_by_rule": 1}
    assert calls == []
    row = json.loads(output_path.read_text(encoding="utf-8").strip())
    assert row["label"]["feedback_type"] == "irrelevant_invalid"
    assert row["rule_hints"]["invalid_reason"] == "test_text"
    assert row["raw_reply"] == ""


def test_experiment_passes_hints_to_llm_for_valid_feedback(tmp_path):
    input_path = tmp_path / "input.jsonl"
    output_path = tmp_path / "output.jsonl"
    input_path.write_text(
        json.dumps({"feedback_id": "f1", "text": "语音输入经常识别错"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    def fake_llm(prompt: str) -> str:
        assert "voice_input" in prompt
        return json.dumps({
            "feedback_type": "bug_problem",
            "product_area": ["voice_input"],
            "issue_pattern": ["incorrect_or_poor_result"],
            "confidence": 0.8,
        }, ensure_ascii=False)

    stats = run_jsonl_experiment(input_path, output_path, llm_call=fake_llm)

    assert stats["skipped_by_rule"] == 0
    row = json.loads(output_path.read_text(encoding="utf-8").strip())
    assert row["rule_hints"]["maybe_product_area"] == ["voice_input"]
    assert row["label"]["product_area"] == ["voice_input"]
