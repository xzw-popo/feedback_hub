"""Evidence-bound, resumable classification for topic-mining candidates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import partial
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Sequence

from feedback_hub.topic_discovery.model_routes import (
    ModelReply,
    ModelRoute,
    QuotaExhaustedError,
    call_openai_compatible_route,
    invoke_model_route,
    run_pauseable_model_jobs,
)

from .config import TopicMiningConfig
from .contracts import TopicSpec
from .retrieval import RecallHit


_PROMPT_PATH = Path(__file__).with_name("prompts") / "classify_system.md"
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)
_REQUIRED_RESULT_FIELDS = frozenset({"item_id", "label", "confidence", "evidence", "reason", "needs_review"})


@dataclass(frozen=True)
class ClassificationResult:
    item_id: str
    label: str
    confidence: float
    evidence: tuple[str, ...]
    reason: str
    needs_review: bool
    source: str = "classifier"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["evidence"] = list(self.evidence)
        return value


def classification_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8").strip()


def build_classification_prompt(
    spec: TopicSpec,
    candidates: Sequence[Mapping[str, Any]],
    *,
    contexts: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    repair_error: str | None = None,
) -> str:
    """Build the user prompt from source evidence only, never recall scores."""
    payload_candidates = []
    for candidate in candidates:
        item_id = _required_text(candidate.get("item_id"), "candidate item_id")
        text = _required_text(candidate.get("text"), f"candidate {item_id} text")
        payload: dict[str, Any] = {"item_id": item_id, "text": text}
        candidate_context = []
        for context in (contexts or {}).get(item_id, ()):
            if not isinstance(context, Mapping):
                continue
            context_text = context.get("text") or context.get("feedback_text")
            if isinstance(context_text, str) and context_text.strip():
                candidate_context.append(context_text.strip())
        if candidate_context:
            payload["context"] = candidate_context
        payload_candidates.append(payload)
    labels = [{"id": entry["id"], "meaning": entry["meaning"]} for entry in spec.classification_labels]
    prompt = {
        "topic_name": spec.topic_name,
        "objective": spec.objective,
        "inclusion_criteria": list(spec.inclusion_criteria),
        "exclusion_criteria": list(spec.exclusion_criteria),
        "positive_examples": list(spec.positive_examples),
        "negative_examples": list(spec.negative_examples),
        "allowed_labels": labels,
        "candidates": payload_candidates,
        "required_result_schema": {
            "results": [{
                "item_id": "candidate item_id", "label": "allowed label", "confidence": "0..1",
                "evidence": ["exact substring from candidate text or context"],
                "reason": "brief rationale", "needs_review": False,
            }],
        },
    }
    if repair_error:
        prompt["repair_instruction"] = (
            "Your prior reply could not be accepted: " + repair_error +
            ". Return a corrected JSON object matching required_result_schema with exact candidate coverage."
        )
    return json.dumps(prompt, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def parse_classification_reply(
    raw: str,
    *,
    expected_ids: set[str],
    allowed_labels: set[str],
) -> list[ClassificationResult]:
    text = _extract_json_text(raw)
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("classification reply must be one JSON object") from exc
    if not isinstance(payload, dict) or set(payload) != {"results"} or not isinstance(payload["results"], list):
        raise ValueError("classification reply must contain only a results array")
    results: list[ClassificationResult] = []
    seen: set[str] = set()
    for row in payload["results"]:
        if not isinstance(row, dict) or set(row) != _REQUIRED_RESULT_FIELDS:
            raise ValueError("classification result has unsupported fields")
        item_id = _required_text(row.get("item_id"), "item_id")
        if item_id in seen:
            raise ValueError(f"duplicate item_id: {item_id}")
        seen.add(item_id)
        label = _required_text(row.get("label"), "label")
        if label not in allowed_labels:
            raise ValueError(f"unsupported label: {label}")
        confidence = row.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
            raise ValueError("confidence must be within 0..1")
        evidence_value = row.get("evidence")
        if not isinstance(evidence_value, list) or any(not isinstance(value, str) or not value.strip() for value in evidence_value):
            raise ValueError("evidence must be an array of non-empty strings")
        evidence = tuple(value.strip() for value in evidence_value)
        if label == "matched" and not evidence:
            raise ValueError("matched results require evidence")
        reason = _required_text(row.get("reason"), "reason")
        if not isinstance(row.get("needs_review"), bool):
            raise ValueError("needs_review must be boolean")
        results.append(ClassificationResult(item_id, label, float(confidence), evidence, reason, row["needs_review"]))
    if seen != expected_ids:
        raise ValueError("classification results must have exact coverage of candidate IDs")
    return results


def validate_evidence(
    results: Sequence[ClassificationResult],
    candidates_by_id: Mapping[str, Mapping[str, Any]],
    *,
    contexts: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> None:
    for result in results:
        candidate = candidates_by_id.get(result.item_id)
        if candidate is None:
            raise ValueError(f"unknown candidate evidence item_id: {result.item_id}")
        source_texts = [_required_text(candidate.get("text"), f"candidate {result.item_id} text")]
        for context in (contexts or {}).get(result.item_id, ()):
            if isinstance(context, Mapping):
                value = context.get("text") or context.get("feedback_text")
                if isinstance(value, str):
                    source_texts.append(value)
        for evidence in result.evidence:
            if not any(evidence in source for source in source_texts):
                raise ValueError(f"evidence is not present in source text for {result.item_id}")


def default_classifier_route() -> ModelRoute:
    values = {
        "api_url": os.environ.get("LLM_API_URL", "").strip(),
        "credential": os.environ.get("LLM_API_KEY", "").strip(),
        "model": os.environ.get("LLM_MODEL", "").strip(),
    }
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise ValueError("missing classifier route configuration: " + ", ".join(missing))
    return ModelRoute("topic_mining_classifier", "openai_compatible", **values)


def classify_candidates(
    spec: TopicSpec,
    candidates: Sequence[RecallHit],
    *,
    artifact_dir: Path,
    contexts: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    routes: Sequence[ModelRoute] | None = None,
    config: TopicMiningConfig | None = None,
    resume: bool = False,
    call_fn: Callable[..., Any] | None = None,
    max_retries: int = 4,
    request_timeout: int = 300,
) -> tuple[list[ClassificationResult], dict[str, Any]]:
    """Classify recall candidates; only complete validated batches are published."""
    config = config or TopicMiningConfig()
    if config.classifier_batch_size < 1 or config.classifier_batch_size > 20:
        raise ValueError("classifier_batch_size must be between 1 and 20")
    if config.classifier_concurrency < 1:
        raise ValueError("classifier_concurrency must be positive")
    normalized = _normalize_candidates(candidates)
    selected_routes = list(routes) if routes is not None else [default_classifier_route()]
    _validate_routes(selected_routes)
    artifact_dir = Path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    batches_path = artifact_dir / "classification_batches.jsonl"
    if resume:
        _retain_successful_checkpoints(batches_path)
    batch_payloads = [normalized[index:index + config.classifier_batch_size] for index in range(0, len(normalized), config.classifier_batch_size)]
    jobs = [(index, f"classification:{index:05d}", {"candidates": batch}) for index, batch in enumerate(batch_payloads)]
    allowed_labels = {entry["id"] for entry in spec.classification_labels}
    openai_call = call_fn or partial(_call_openai_classifier, system_prompt=classification_system_prompt())

    def worker(_index: int, key: str, payload: dict[str, Any], route: ModelRoute) -> tuple[dict[str, Any], str | None]:
        batch = payload["candidates"]
        expected_ids = {row["item_id"] for row in batch}
        candidates_by_id = {row["item_id"]: row for row in batch}
        raw_replies: list[str] = []
        parser_errors: list[str] = []
        attempts = 0
        elapsed_ms = 0
        retry_chain: list[str] = []
        results: list[ClassificationResult] = []
        call_error: str | None = None
        for attempt in range(2):
            prompt = build_classification_prompt(spec, batch, contexts=contexts, repair_error=parser_errors[-1] if parser_errors else None)
            try:
                reply = invoke_model_route(prompt, route=route, call_fn=openai_call, max_retries=max_retries, timeout=request_timeout)
            except Exception as exc:
                # QuotaExhaustedError is intentionally not flattened: the scheduler
                # must stop submission and preserve its pause checkpoint.
                if isinstance(exc, QuotaExhaustedError):
                    raise
                call_error = f"{type(exc).__name__}: {exc}"
                break
            raw_replies.append(_redact(reply.content, route.credential))
            attempts += reply.attempts
            elapsed_ms += reply.elapsed_ms
            retry_chain.extend(reply.retry_chain)
            try:
                results = parse_classification_reply(reply.content, expected_ids=expected_ids, allowed_labels=allowed_labels)
                validate_evidence(results, candidates_by_id, contexts=contexts)
                break
            except ValueError as exc:
                parser_errors.append(f"{type(exc).__name__}: {exc}")
        error = call_error or (parser_errors[-1] if not results else None)
        row = {
            "batch_key": key,
            "item_ids": sorted(expected_ids),
            "results": [result.to_dict() for result in results] if error is None else [],
            "raw_replies": raw_replies,
            "parse_errors": parser_errors,
            "parse_attempts": len(raw_replies),
            "call_error": call_error,
            "route_source": route.name,
            "endpoint_class": route.endpoint_class,
            "model": route.model,
            "attempts": attempts,
            "retry_chain": retry_chain,
            "elapsed_ms": elapsed_ms,
        }
        return row, error

    rows, scheduler_stats = run_pauseable_model_jobs(
        jobs, worker, routes=selected_routes, output_path=batches_path,
        concurrency_per_route=config.classifier_concurrency, resume=resume,
    )
    _write_audit(artifact_dir / "classification_audit.jsonl", rows, append=resume)
    ready = scheduler_stats["run_status"] == "completed" and scheduler_stats["failed"] == 0
    results = _flatten_complete_results(rows, normalized) if ready else []
    classified_path = artifact_dir / "classified.jsonl"
    if ready:
        classified_path.write_text("".join(json.dumps(row.to_dict(), ensure_ascii=False, sort_keys=True) + "\n" for row in results), encoding="utf-8")
    else:
        classified_path.unlink(missing_ok=True)
    stats = dict(scheduler_stats)
    stats["classification_status"] = "review_ready" if ready else "unresolved"
    stats["batches"] = len(jobs)
    stats["classified"] = len(results)
    return results, stats


def _normalize_candidates(candidates: Sequence[RecallHit]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for hit in candidates:
        item = hit.item if isinstance(hit, RecallHit) else getattr(hit, "item", None)
        if not isinstance(item, Mapping):
            raise ValueError("classification candidate must be a RecallHit with an item")
        item_id = _required_text(getattr(hit, "item_id", item.get("item_id")), "candidate item_id")
        if item_id in seen:
            raise ValueError(f"classification candidates contain duplicate item_id: {item_id}")
        seen.add(item_id)
        normalized.append({"item_id": item_id, "text": _required_text(item.get("text"), f"candidate {item_id} text")})
    return sorted(normalized, key=lambda row: row["item_id"])


def _call_openai_classifier(
    user_prompt: str,
    *,
    system_prompt: str,
    route: ModelRoute,
    max_retries: int,
    timeout: int,
) -> ModelReply:
    """Adapt the committed two-message OpenAI client to the common job runner.

    The shared client exposes HTTP 429 as ``openai_http_429``.  Translate that
    one stable condition into the scheduler's typed pause signal; no credential
    or response body is placed in an artifact.
    """
    try:
        return call_openai_compatible_route(
            system_prompt, user_prompt, route=route, max_retries=max_retries, timeout=timeout,
        )
    except RuntimeError as exc:
        if str(exc) == "openai_http_429":
            raise QuotaExhaustedError(route.name, 429, "openai_http_429") from exc
        raise


def _validate_routes(routes: Sequence[ModelRoute]) -> None:
    if not routes:
        raise ValueError("at least one classifier route is required")
    names: set[str] = set()
    for route in routes:
        if not route.name or route.name in names:
            raise ValueError("classifier routes require unique names")
        names.add(route.name)
        if route.endpoint_class != "openai_compatible":
            raise ValueError("classifier routes must be openai_compatible")
        for field in ("api_url", "credential", "model"):
            if not getattr(route, field).strip():
                raise ValueError(f"classifier route requires {field}")


def _flatten_complete_results(rows: Sequence[Mapping[str, Any]], candidates: Sequence[Mapping[str, Any]]) -> list[ClassificationResult]:
    expected_ids = {row["item_id"] for row in candidates}
    flattened: list[ClassificationResult] = []
    for row in rows:
        # A repaired second reply keeps the first parser error in the audit
        # trail, but is a complete batch once it has validated results.
        if row.get("call_error") or not row.get("results"):
            raise ValueError("cannot flatten unresolved classification batches")
        values = row.get("results")
        if not isinstance(values, list):
            raise ValueError("classification batch is missing results")
        flattened.extend(ClassificationResult(
            item_id=value["item_id"], label=value["label"], confidence=float(value["confidence"]),
            evidence=tuple(value["evidence"]), reason=value["reason"], needs_review=bool(value["needs_review"]), source=value.get("source", "classifier"),
        ) for value in values)
    if {value.item_id for value in flattened} != expected_ids or len(flattened) != len(expected_ids):
        raise ValueError("complete classification batches do not have exact candidate coverage")
    return sorted(flattened, key=lambda value: value.item_id)


def _retain_successful_checkpoints(output_path: Path) -> None:
    checkpoint = output_path.with_suffix(output_path.suffix + ".checkpoint.jsonl")
    if not checkpoint.exists():
        return
    good = []
    for line in checkpoint.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("ok") is True:
            good.append(json.dumps(record, ensure_ascii=False, sort_keys=True))
    checkpoint.write_text("".join(line + "\n" for line in good), encoding="utf-8")


def _write_audit(path: Path, rows: Sequence[Mapping[str, Any]], *, append: bool) -> None:
    mode = "a" if append and path.exists() else "w"
    with path.open(mode, encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def _extract_json_text(raw: str) -> str:
    if not isinstance(raw, str):
        raise ValueError("classification reply must be text")
    match = _FENCE_RE.match(raw)
    return match.group(1) if match else raw.strip()


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "[REDACTED]") if secret else text
