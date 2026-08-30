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

    assert stats == {"total": 1, "labeled": 1, "failed": 0, "skipped_by_rule": 0}
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
