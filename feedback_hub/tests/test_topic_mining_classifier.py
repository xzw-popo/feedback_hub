from __future__ import annotations

import json

import pytest

from feedback_hub.topic_discovery.model_routes import ModelReply, ModelRoute, QuotaExhaustedError
from feedback_hub.topic_mining.classifier import (
    ClassificationResult,
    build_classification_prompt,
    classify_candidates,
    parse_classification_reply,
    validate_evidence,
)
from feedback_hub.topic_mining.config import TopicMiningConfig
from feedback_hub.topic_mining.contracts import validate_topic_spec
from feedback_hub.topic_mining.retrieval import RecallHit


@pytest.fixture
def valid_topic_spec():
    return validate_topic_spec({
        "schema_version": 1,
        "topic_name": "全屏时工具栏不隐藏",
        "objective": "找出全屏时输入法工具栏仍显示的反馈",
        "scope": {"start_time": "2026-01-01T00:00:00+00:00", "end_time": "2026-01-02T00:00:00+00:00", "platforms": ["Win"]},
        "unit": "feedback",
        "inclusion_criteria": ["全屏时输入法工具栏仍显示"],
        "exclusion_criteria": ["Windows 系统任务栏不隐藏"],
        "positive_examples": ["游戏全屏时工具栏一直显示"],
        "negative_examples": ["Windows 任务栏没有隐藏"],
        "lexical_hints": {"objects": ["工具栏"], "contexts": ["全屏", "游戏"]},
        "classification_labels": [{"id": "matched", "meaning": "符合"}, {"id": "not_matched", "meaning": "不符合或证据不足"}],
        "output": {"preferred_format": "jsonl", "required_fields": ["feedback_text"]},
    })


def candidate_payload(item_id: str = "a", text: str = "全屏时工具栏一直显示") -> dict:
    return {"item_id": item_id, "text": text, "source_url": "https://example.test/a"}


def recall(item_id: str, text: str = "全屏时工具栏一直显示") -> RecallHit:
    return RecallHit(item_id, candidate_payload(item_id, text), ("vector",), 0.9, 1, {}, {}, ("positive:0",), ())


def result(item_id: str = "a", **changes) -> ClassificationResult:
    values = {
        "item_id": item_id, "label": "matched", "confidence": 0.9,
        "evidence": ("工具栏一直显示",), "reason": "符合", "needs_review": False,
    }
    values.update(changes)
    return ClassificationResult(**values)


def test_prompt_contains_topic_boundaries_but_not_retrieval_scores(valid_topic_spec):
    prompt = build_classification_prompt(valid_topic_spec, [candidate_payload()])
    assert "Windows 系统任务栏不隐藏" in prompt
    assert "全屏时输入法工具栏仍显示" in prompt
    assert "rrf_score" not in prompt
    assert "vector_score" not in prompt
    assert "fused_score" not in prompt


def test_parser_requires_exact_candidate_coverage(valid_topic_spec):
    raw = '{"results":[{"item_id":"a","label":"matched","confidence":0.9,"evidence":["工具栏一直显示"],"reason":"全屏未隐藏","needs_review":false}]}'
    with pytest.raises(ValueError, match="exact coverage"):
        parse_classification_reply(raw, expected_ids={"a", "b"}, allowed_labels={"matched", "not_matched"})


def test_parser_repairs_json_fence_and_rejects_invalid_result_values():
    fenced = '```json\n{"results":[{"item_id":"a","label":"matched","confidence":0.9,"evidence":["工具栏一直显示"],"reason":"符合","needs_review":false}]}\n```'
    assert parse_classification_reply(fenced, expected_ids={"a"}, allowed_labels={"matched", "not_matched"})[0].item_id == "a"
    for field, value, match in [("confidence", 1.1, "confidence"), ("label", "other", "label"), ("evidence", [], "evidence")]:
        body = {"item_id": "a", "label": "matched", "confidence": 0.9, "evidence": ["工具栏一直显示"], "reason": "符合", "needs_review": False}
        body[field] = value
        with pytest.raises(ValueError, match=match):
            parse_classification_reply(json.dumps({"results": [body]}), expected_ids={"a"}, allowed_labels={"matched", "not_matched"})


def test_parser_rejects_duplicate_ids():
    raw = {"results": [
        {"item_id": "a", "label": "not_matched", "confidence": 0.5, "evidence": [], "reason": "不足", "needs_review": False},
        {"item_id": "a", "label": "not_matched", "confidence": 0.5, "evidence": [], "reason": "不足", "needs_review": False},
    ]}
    with pytest.raises(ValueError, match="duplicate"):
        parse_classification_reply(json.dumps(raw), expected_ids={"a"}, allowed_labels={"matched", "not_matched"})


@pytest.mark.parametrize("field, value", [("item_id", " a "), ("label", " matched ")])
def test_parser_rejects_whitespace_normalized_identity_values(field, value):
    body = {"item_id": "a", "label": "matched", "confidence": 0.9, "evidence": ["工具栏一直显示"], "reason": "符合", "needs_review": False}
    body[field] = value
    with pytest.raises(ValueError):
        parse_classification_reply(json.dumps({"results": [body]}), expected_ids={"a"}, allowed_labels={"matched", "not_matched"})


def test_evidence_keeps_original_whitespace_and_rejects_nonliteral_substring():
    raw = json.dumps({"results": [{"item_id": "a", "label": "matched", "confidence": 0.9, "evidence": [" 工具栏一直显示 "], "reason": "符合", "needs_review": False}]})
    parsed = parse_classification_reply(raw, expected_ids={"a"}, allowed_labels={"matched", "not_matched"})
    assert parsed[0].evidence == (" 工具栏一直显示 ",)
    with pytest.raises(ValueError, match="evidence"):
        validate_evidence(parsed, {"a": candidate_payload()})


def test_parser_rejects_evidence_not_present_in_text():
    with pytest.raises(ValueError, match="evidence"):
        validate_evidence([result(evidence=("不存在的事实",))], {"a": candidate_payload(text="原文")})


def _route() -> ModelRoute:
    return ModelRoute("classifier", "openai_compatible", "https://model.test", "not-a-secret", "test-model")


def _named_route(name: str, credential: str) -> ModelRoute:
    return ModelRoute(name, "openai_compatible", f"https://{name}.test", credential, "test-model")


def test_classification_retries_once_after_parse_failure_and_writes_complete_outputs(tmp_path, valid_topic_spec):
    calls = []

    def call_fn(_prompt, *, route, **_kwargs):
        calls.append(route.name)
        content = "not json" if len(calls) == 1 else json.dumps({"results": [{"item_id": "a", "label": "matched", "confidence": 0.9, "evidence": ["工具栏一直显示"], "reason": "符合", "needs_review": False}]})
        return ModelReply(content, route.name, route.endpoint_class, route.model, 1, 2, ())

    rows, stats = classify_candidates(valid_topic_spec, [recall("a")], artifact_dir=tmp_path, routes=[_route()], config=TopicMiningConfig(classifier_batch_size=20, classifier_concurrency=1), call_fn=call_fn)

    assert [row.item_id for row in rows] == ["a"]
    assert stats["run_status"] == "completed"
    assert stats["classification_status"] == "review_ready"
    assert len(calls) == 2
    assert len((tmp_path / "classified.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    audit = json.loads((tmp_path / "classification_audit.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert audit["parse_attempts"] == 2
    assert audit["raw_replies"][0] == "not json"
    assert json.loads(audit["raw_replies"][1])["results"][0]["item_id"] == "a"
    assert "not-a-secret" not in (tmp_path / "classification_audit.jsonl").read_text(encoding="utf-8")


def test_classification_does_not_publish_partial_batches_and_resumes(tmp_path, valid_topic_spec):
    attempts: list[str] = []

    def fail_then_succeed(_prompt, *, route, **_kwargs):
        attempts.append(route.name)
        if len(attempts) <= 2:
            return "not json"
        return json.dumps({"results": [{"item_id": "a", "label": "not_matched", "confidence": 0.9, "evidence": [], "reason": "不足", "needs_review": False}]})

    config = TopicMiningConfig(classifier_batch_size=1, classifier_concurrency=1)
    first, first_stats = classify_candidates(valid_topic_spec, [recall("a")], artifact_dir=tmp_path, routes=[_route()], config=config, call_fn=fail_then_succeed)
    assert first == []
    assert first_stats["classification_status"] == "unresolved"
    assert not (tmp_path / "classified.jsonl").exists()
    second, second_stats = classify_candidates(valid_topic_spec, [recall("a")], artifact_dir=tmp_path, routes=[_route()], config=config, call_fn=fail_then_succeed, resume=True)
    assert [row.item_id for row in second] == ["a"]
    assert second_stats["classification_status"] == "review_ready"


def test_quota_pause_keeps_classification_unpublished(tmp_path, valid_topic_spec):
    def call_fn(_prompt, *, route, **_kwargs):
        raise QuotaExhaustedError(route.name, 429, "quota")

    rows, stats = classify_candidates(valid_topic_spec, [recall("a")], artifact_dir=tmp_path, routes=[_route()], config=TopicMiningConfig(classifier_concurrency=1), call_fn=call_fn)
    assert rows == []
    assert stats["run_status"] == "paused_quota_exhausted"
    assert not (tmp_path / "classified.jsonl").exists()


def test_parse_failure_then_quota_keeps_partial_audit_and_resume_publishes(tmp_path, valid_topic_spec):
    calls = []

    def call_fn(_prompt, *, route, **_kwargs):
        calls.append(route.name)
        if len(calls) == 1:
            return "not json"
        if len(calls) == 2:
            raise QuotaExhaustedError(route.name, 429, "quota")
        return json.dumps({"results": [{"item_id": "a", "label": "matched", "confidence": 0.9, "evidence": ["工具栏一直显示"], "reason": "符合", "needs_review": False}]})

    config = TopicMiningConfig(classifier_batch_size=1, classifier_concurrency=1)
    first, first_stats = classify_candidates(valid_topic_spec, [recall("a")], artifact_dir=tmp_path, routes=[_route()], config=config, call_fn=call_fn)
    assert first == []
    assert first_stats["run_status"] == "paused_quota_exhausted"
    audit_lines = [json.loads(line) for line in (tmp_path / "classification_audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(audit_lines) == 1
    assert audit_lines[0]["raw_replies"] == ["not json"]
    assert audit_lines[0]["parse_errors"]
    assert audit_lines[0]["route_source"] == "classifier"
    assert audit_lines[0]["model"] == "test-model"
    assert audit_lines[0]["item_ids"] == ["a"]
    assert not (tmp_path / "classified.jsonl").exists()

    second, second_stats = classify_candidates(valid_topic_spec, [recall("a")], artifact_dir=tmp_path, routes=[_route()], config=config, call_fn=call_fn, resume=True)
    assert [row.item_id for row in second] == ["a"]
    assert second_stats["classification_status"] == "review_ready"
    assert len((tmp_path / "classification_audit.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_audit_generations_preserve_repeated_quota_history_and_redact_every_route_secret(tmp_path, valid_topic_spec):
    route_a = _named_route("route-a", "secret-a")
    route_b = _named_route("route-b", "secret-b")
    calls = []

    def call_fn(_prompt, *, route, **_kwargs):
        calls.append(route.name)
        if len(calls) in {1, 3}:
            return f"{route_b.credential} invalid json"
        if len(calls) in {2, 4}:
            raise QuotaExhaustedError(route.name, 429, "quota")
        return json.dumps({"results": [{"item_id": "a", "label": "matched", "confidence": 0.9, "evidence": ["工具栏一直显示"], "reason": "符合", "needs_review": False}]})

    config = TopicMiningConfig(classifier_batch_size=1, classifier_concurrency=1)
    for resume in (False, True):
        rows, stats = classify_candidates(valid_topic_spec, [recall("a")], artifact_dir=tmp_path, routes=[route_a, route_b], config=config, call_fn=call_fn, resume=resume)
        assert rows == []
        assert stats["run_status"] == "paused_quota_exhausted"
    rows, stats = classify_candidates(valid_topic_spec, [recall("a")], artifact_dir=tmp_path, routes=[route_a, route_b], config=config, call_fn=call_fn, resume=True)
    assert [row.item_id for row in rows] == ["a"]
    assert stats["classification_status"] == "review_ready"

    audit_rows = [json.loads(line) for line in (tmp_path / "classification_audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["audit_generation"] for row in audit_rows] == [1, 2, 3]
    assert len({row["audit_id"] for row in audit_rows}) == 3
    persisted = "\n".join(path.read_text(encoding="utf-8") for path in tmp_path.rglob("*") if path.is_file())
    assert route_a.credential not in persisted
    assert route_b.credential not in persisted


def test_missing_route_credentials_fails_before_scheduling(tmp_path, valid_topic_spec):
    route = ModelRoute("classifier", "openai_compatible", "", "", "")
    with pytest.raises(ValueError, match="api_url"):
        classify_candidates(valid_topic_spec, [recall("a")], artifact_dir=tmp_path, routes=[route], config=TopicMiningConfig())
