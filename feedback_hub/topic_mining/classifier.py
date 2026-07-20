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
        item_id = _exact_required_text(row.get("item_id"), "item_id")
        if item_id in seen:
            raise ValueError(f"duplicate item_id: {item_id}")
        seen.add(item_id)
        label = _exact_required_text(row.get("label"), "label")
        if label not in allowed_labels:
            raise ValueError(f"unsupported label: {label}")
        confidence = row.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
            raise ValueError("confidence must be within 0..1")
        evidence_value = row.get("evidence")
        if not isinstance(evidence_value, list) or any(not isinstance(value, str) or not value.strip() for value in evidence_value):
            raise ValueError("evidence must be an array of non-empty strings")
        # Evidence must remain byte-for-byte identical to the model response.
        # Whitespace can change substring semantics, so only use strip to test
        # whether it is blank and validate the original text below.
        evidence = tuple(evidence_value)
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
    cancel_check: Callable[[], None] | None = None,
    progress_callback: Callable[[], None] | None = None,
    max_retries: int = 4,
    request_timeout: int = 300,
) -> tuple[list[ClassificationResult], dict[str, Any]]:
    """Classify recall candidates; only complete validated batches are published."""
    _check_cancel(cancel_check)
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
    partial_audit_dir = artifact_dir / "classification_partial_audit"
    audit_path = artifact_dir / "classification_audit.jsonl"
    audit_generation = _next_audit_generation(audit_path, partial_audit_dir)
    if resume:
        _retain_successful_checkpoints(batches_path)
    batch_payloads = [normalized[index:index + config.classifier_batch_size] for index in range(0, len(normalized), config.classifier_batch_size)]
    jobs = [(index, f"classification:{index:05d}", {"candidates": batch}) for index, batch in enumerate(batch_payloads)]
    allowed_labels = {entry["id"] for entry in spec.classification_labels}
    openai_call = call_fn or partial(_call_openai_classifier, system_prompt=classification_system_prompt())
    route_secrets = tuple(route.credential for route in selected_routes if route.credential)

    def worker(_index: int, key: str, payload: dict[str, Any], route: ModelRoute) -> tuple[dict[str, Any], str | None]:
        _check_cancel(cancel_check)
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
            _check_cancel(cancel_check)
            prompt = build_classification_prompt(spec, batch, contexts=contexts, repair_error=parser_errors[-1] if parser_errors else None)
            try:
                reply = invoke_model_route(prompt, route=route, call_fn=openai_call, max_retries=max_retries, timeout=request_timeout)
            except QuotaExhaustedError:
                _check_cancel(cancel_check)
                # The shared scheduler intentionally does not retain a future
                # that raises quota. Persist this already-safe partial record
                # by batch key before re-raising so the main thread can merge
                # it after all workers settle.
                _write_partial_audit(
                    partial_audit_dir,
                    key,
                    _classification_audit_row(
                        key=key,
                        expected_ids=expected_ids,
                        results=(),
                        raw_replies=raw_replies,
                        parser_errors=parser_errors,
                        call_error=None,
                        route=route,
                        attempts=attempts,
                        retry_chain=retry_chain,
                        elapsed_ms=elapsed_ms,
                        audit_id=f"g{audit_generation}:{key}:quota:{len(raw_replies)}",
                        audit_generation=audit_generation,
                    ),
                    route_secrets,
                    audit_generation,
                )
                raise
            except Exception as exc:
                # QuotaExhaustedError is intentionally not flattened: the scheduler
                # must stop submission and preserve its pause checkpoint.
                if isinstance(exc, QuotaExhaustedError):
                    raise
                call_error = f"{type(exc).__name__}: {exc}"
                break
            _check_cancel(cancel_check)
            raw_replies.append(_redact(reply.content, route_secrets))
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
        row = _classification_audit_row(
            key=key,
            expected_ids=expected_ids,
            results=results if error is None else (),
            raw_replies=raw_replies,
            parser_errors=parser_errors,
            call_error=call_error,
            route=route,
            attempts=attempts,
            retry_chain=retry_chain,
            elapsed_ms=elapsed_ms,
            audit_id=f"g{audit_generation}:{key}:final:{len(raw_replies)}",
            audit_generation=audit_generation,
        )
        _check_cancel(cancel_check)
        return row, error

    rows, scheduler_stats = run_pauseable_model_jobs(
        jobs, worker, routes=selected_routes, output_path=batches_path,
        concurrency_per_route=config.classifier_concurrency, resume=resume,
        progress_callback=progress_callback,
    )
    _check_cancel(cancel_check)
    partial_rows = _read_partial_audit(partial_audit_dir)
    # Audit history is append-only across both new and resumed attempts. A new
    # scheduling attempt may reset checkpoints, but must not erase evidence of
    # an earlier unresolved batch in the same isolated run directory.
    _write_audit(audit_path, [*partial_rows, *rows], append=True, secrets=route_secrets)
    _clear_partial_audit(partial_audit_dir)
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


def _check_cancel(cancel_check: Callable[[], None] | None) -> None:
    if cancel_check is not None:
        cancel_check()


def _classification_audit_row(
    *,
    key: str,
    expected_ids: set[str],
    results: Sequence[ClassificationResult],
    raw_replies: Sequence[str],
    parser_errors: Sequence[str],
    call_error: str | None,
    route: ModelRoute,
    attempts: int,
    retry_chain: Sequence[str],
    elapsed_ms: int,
    audit_id: str,
    audit_generation: int,
) -> dict[str, Any]:
    return {
        "audit_id": audit_id,
        "audit_generation": audit_generation,
        "batch_key": key,
        "item_ids": sorted(expected_ids),
        "results": [result.to_dict() for result in results],
        "raw_replies": list(raw_replies),
        "parse_errors": list(parser_errors),
        "parse_attempts": len(raw_replies),
        "call_error": call_error,
        "route_source": route.name,
        "endpoint_class": route.endpoint_class,
        "model": route.model,
        "attempts": attempts,
        "retry_chain": list(retry_chain),
        "elapsed_ms": elapsed_ms,
    }


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


def _write_partial_audit(
    directory: Path,
    batch_key: str,
    row: Mapping[str, Any],
    secrets: Sequence[str],
    audit_generation: int,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"g{audit_generation}_{batch_key.replace(':', '_')}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(_redact_nested(dict(row), secrets), ensure_ascii=False, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _read_partial_audit(directory: Path) -> list[dict[str, Any]]:
    if not directory.exists():
        return []
    rows = []
    for path in sorted(directory.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _clear_partial_audit(directory: Path) -> None:
    if not directory.exists():
        return
    for path in directory.glob("*.json"):
        path.unlink(missing_ok=True)
    directory.rmdir()


def _next_audit_generation(audit_path: Path, partial_directory: Path) -> int:
    generations: list[int] = []
    if audit_path.exists():
        for line in audit_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, Mapping) and isinstance(row.get("audit_generation"), int):
                generations.append(row["audit_generation"])
    if partial_directory.exists():
        for partial_path in partial_directory.glob("*.json"):
            try:
                row = json.loads(partial_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(row, Mapping) and isinstance(row.get("audit_generation"), int):
                generations.append(row["audit_generation"])
    return max(generations, default=0) + 1


def _write_audit(path: Path, rows: Sequence[Mapping[str, Any]], *, append: bool, secrets: Sequence[str]) -> None:
    existing: list[dict[str, Any]] = []
    if append and path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                existing.append(_redact_nested(value, secrets))
    merged = {str(row.get("audit_id") or f"legacy:{index}"): dict(row) for index, row in enumerate(existing)}
    for index, row in enumerate(rows):
        merged[str(row.get("audit_id") or f"new:{index}")] = _redact_nested(dict(row), secrets)
    ordered = [merged[key] for key in sorted(merged)]
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in ordered), encoding="utf-8")
    temporary.replace(path)


def _extract_json_text(raw: str) -> str:
    if not isinstance(raw, str):
        raise ValueError("classification reply must be text")
    match = _FENCE_RE.match(raw)
    return match.group(1) if match else raw.strip()


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _exact_required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be an exact non-empty string")
    return value


def _redact(text: str, secrets: Sequence[str]) -> str:
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def _redact_nested(value: Any, secrets: Sequence[str]) -> Any:
    if isinstance(value, str):
        return _redact(value, secrets)
    if isinstance(value, list):
        return [_redact_nested(item, secrets) for item in value]
    if isinstance(value, tuple):
        return [_redact_nested(item, secrets) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _redact_nested(item, secrets) for key, item in value.items()}
    return value
