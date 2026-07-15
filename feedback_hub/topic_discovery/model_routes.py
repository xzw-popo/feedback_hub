"""Quota-aware model routes and resumable incremental scheduling."""
from __future__ import annotations

from collections import Counter, deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
import json
import os
from pathlib import Path
import time
from typing import Any, Callable, Optional

import requests


@dataclass(frozen=True)
class ModelRoute:
    name: str
    endpoint_class: str
    api_url: str
    credential: str
    model: str = ""
    api_user: str = ""


@dataclass(frozen=True)
class ModelReply:
    content: str
    route_name: str
    endpoint_class: str
    model: str
    attempts: int
    elapsed_ms: int
    retry_chain: tuple[str, ...]


class QuotaExhaustedError(RuntimeError):
    def __init__(self, route_name: str, status_code: int, response_excerpt: str):
        super().__init__(f"quota_exhausted:{route_name}:{status_code}")
        self.route_name = route_name
        self.status_code = status_code
        self.response_excerpt = response_excerpt[:200]


Job = tuple[int, str, dict[str, Any]]
Worker = Callable[[int, str, dict[str, Any], ModelRoute], tuple[dict[str, Any], Optional[str]]]

_QUOTA_MARKERS = (
    "quota",
    "rate limit",
    "次数上限",
    "额度已用完",
    "超过限额",
)
_TRANSIENT_HTTP_STATUSES = {502, 503, 504}
_SECRET_KEY_MARKERS = ("token", "secret", "api_key", "authorization", "credential")


def call_knot_route(
    prompt: str,
    *,
    route: ModelRoute,
    timeout: int = 300,
    max_retries: int = 4,
    http_post: Callable[..., Any] = requests.post,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> ModelReply:
    if route.endpoint_class != "knot_agent":
        raise ValueError("call_knot_route requires a knot_agent route")
    body: dict[str, Any] = {
        "input": {
            "message": prompt,
            "conversation_id": "",
            "stream": False,
            "enable_web_search": False,
            "temperature": 0,
        }
    }
    if route.model:
        body["input"]["model"] = route.model
    headers = {"x-knot-api-token": route.credential}
    if route.api_user:
        headers["x-knot-api-user"] = route.api_user

    started = time.time()
    retry_chain: list[str] = []
    response = None
    attempts = 0
    for attempt in range(max_retries + 1):
        attempts = attempt + 1
        try:
            response = http_post(
                route.api_url,
                json=body,
                headers=headers,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            if attempt >= max_retries:
                raise RuntimeError(f"knot_request_failed:{type(exc).__name__}") from exc
            retry_chain.append(type(exc).__name__)
            sleep_fn(min(20, 2 ** attempt))
            continue

        payload = _response_payload(response)
        top_level_error = _top_level_error(payload)
        if response.status_code == 429 or (
            response.status_code != 200 and _contains_quota_marker(top_level_error)
        ):
            raise QuotaExhaustedError(
                route.name,
                int(response.status_code),
                top_level_error or str(getattr(response, "text", "")),
            )
        if response.status_code in _TRANSIENT_HTTP_STATUSES and attempt < max_retries:
            retry_chain.append(f"http_{response.status_code}")
            sleep_fn(min(20, 2 ** attempt))
            continue
        break

    if response is None:
        raise RuntimeError("knot_request_failed:no_response")
    if response.status_code != 200:
        raise RuntimeError(f"knot_http_{response.status_code}:{_top_level_error(_response_payload(response))[:200]}")
    content = _extract_knot_content(_response_payload(response))
    return ModelReply(
        content=content,
        route_name=route.name,
        endpoint_class=route.endpoint_class,
        model=route.model or "agent_default",
        attempts=attempts,
        elapsed_ms=int((time.time() - started) * 1000),
        retry_chain=tuple(retry_chain),
    )


def call_openai_compatible_route(
    system_prompt: str,
    user_prompt: str,
    *,
    route: ModelRoute,
    timeout: int = 300,
    max_retries: int = 4,
    http_post: Callable[..., Any] = requests.post,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> ModelReply:
    if route.endpoint_class != "openai_compatible":
        raise ValueError("call_openai_compatible_route requires an openai_compatible route")
    if not route.model:
        raise ValueError("OpenAI-compatible route requires a model")
    body = {
        "model": route.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
    }
    headers = {
        "Authorization": f"Bearer {route.credential}",
        "Content-Type": "application/json",
    }
    started = time.time()
    retry_chain: list[str] = []
    response = None
    attempts = 0
    for attempt in range(max_retries + 1):
        attempts = attempt + 1
        try:
            response = http_post(
                route.api_url,
                json=body,
                headers=headers,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            if attempt >= max_retries:
                raise RuntimeError(f"openai_request_failed:{type(exc).__name__}") from exc
            retry_chain.append(type(exc).__name__)
            sleep_fn(min(20, 2 ** attempt))
            continue
        if response.status_code in {429, 500, 502, 503, 504} and attempt < max_retries:
            retry_chain.append(f"http_{response.status_code}")
            sleep_fn(min(20, 2 ** attempt))
            continue
        break
    if response is None:
        raise RuntimeError("openai_request_failed:no_response")
    if response.status_code != 200:
        raise RuntimeError(f"openai_http_{response.status_code}")
    payload = _response_payload(response)
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("openai_unknown_response_shape") from exc
    if not isinstance(content, str):
        raise RuntimeError("openai_response_content_must_be_text")
    return ModelReply(
        content=content,
        route_name=route.name,
        endpoint_class=route.endpoint_class,
        model=route.model,
        attempts=attempts,
        elapsed_ms=int((time.time() - started) * 1000),
        retry_chain=tuple(retry_chain),
    )


def normalize_knot_routes(routes: list[Any]) -> list[ModelRoute]:
    normalized: list[ModelRoute] = []
    names: set[str] = set()
    for value in routes:
        if isinstance(value, ModelRoute):
            route = value
        elif isinstance(value, dict):
            route = ModelRoute(
                name=str(value.get("name") or ""),
                endpoint_class="knot_agent",
                api_url=str(value.get("api_url") or ""),
                credential=str(value.get("token") or value.get("credential") or ""),
                model=str(value.get("model") or ""),
                api_user=str(value.get("api_user") or ""),
            )
        else:
            raise ValueError("model route must be a ModelRoute or mapping")
        if (
            not route.name
            or route.endpoint_class != "knot_agent"
            or not route.api_url
            or not route.credential
            or route.name in names
        ):
            raise ValueError("each Knot route requires a unique name, api_url, and token")
        names.add(route.name)
        normalized.append(route)
    if not normalized:
        raise ValueError("at least one Knot route is required")
    return normalized


def invoke_model_route(
    prompt: str,
    *,
    route: ModelRoute,
    call_fn: Callable[..., Any] = call_knot_route,
    max_retries: int = 4,
    timeout: int = 300,
) -> ModelReply:
    started = time.time()
    result = call_fn(
        prompt,
        route=route,
        max_retries=max_retries,
        timeout=timeout,
    )
    if isinstance(result, ModelReply):
        return result
    if isinstance(result, str):
        return ModelReply(
            content=result,
            route_name=route.name,
            endpoint_class=route.endpoint_class,
            model=route.model or "agent_default",
            attempts=1,
            elapsed_ms=int((time.time() - started) * 1000),
            retry_chain=(),
        )
    raise TypeError("model call must return ModelReply or str")


def run_pauseable_model_jobs(
    jobs: list[Job],
    worker: Worker,
    *,
    routes: list[ModelRoute],
    output_path: str | Path,
    concurrency_per_route: int = 4,
    resume: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not routes:
        raise ValueError("at least one model route is required")
    if concurrency_per_route < 1:
        raise ValueError("concurrency_per_route must be positive")
    indexes = [index for index, _key, _payload in jobs]
    keys = [key for _index, key, _payload in jobs]
    if len(indexes) != len(set(indexes)) or len(keys) != len(set(keys)):
        raise ValueError("job indexes and keys must be unique")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output_path.with_suffix(output_path.suffix + ".checkpoint.jsonl")
    failures_path = output_path.with_suffix(output_path.suffix + ".failures.jsonl")
    state_path = output_path.with_suffix(output_path.suffix + ".run_state.json")
    expected = {index: key for index, key, _payload in jobs}
    records = _load_checkpoint(checkpoint_path, expected) if resume else {}
    if not resume:
        checkpoint_path.write_text("", encoding="utf-8")
        output_path.unlink(missing_ok=True)
        failures_path.unlink(missing_ok=True)

    resumed_count = len(records)
    pending = deque(job for job in jobs if job[0] not in records)
    secrets = tuple(route.credential for route in routes if route.credential)
    quota_error: QuotaExhaustedError | None = None
    route_cursor = 0

    _write_json_atomic(state_path, {
        "run_status": "running",
        "completed": len(records),
        "remaining": len(jobs) - len(records),
    })

    max_workers = len(routes) * concurrency_per_route
    with checkpoint_path.open("a", encoding="utf-8") as checkpoint:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures: dict[Future, tuple[int, str]] = {}

            def submit_available() -> None:
                nonlocal route_cursor
                while pending and len(futures) < max_workers and quota_error is None:
                    index, key, payload = pending.popleft()
                    route = routes[route_cursor % len(routes)]
                    route_cursor += 1
                    future = executor.submit(worker, index, key, payload, route)
                    futures[future] = (index, key)

            submit_available()
            while futures:
                completed_futures, _waiting = wait(futures, return_when=FIRST_COMPLETED)
                batch_results: list[tuple[int, str, dict[str, Any], str | None]] = []
                for future in completed_futures:
                    index, key = futures.pop(future)
                    try:
                        row, error = future.result()
                        if not isinstance(row, dict):
                            raise TypeError("worker row must be a dict")
                    except QuotaExhaustedError as exc:
                        if quota_error is None:
                            quota_error = exc
                        continue
                    except Exception as exc:
                        row = {"batch_key": key}
                        error = f"{type(exc).__name__}: {exc}"
                    batch_results.append((index, key, row, error))

                for index, key, row, error in sorted(batch_results, key=lambda value: value[0]):
                    safe_row = _redact_secrets(row, secrets)
                    record = {
                        "index": index,
                        "key": key,
                        "ok": error is None,
                        "error": _redact_secrets(error, secrets),
                        "row": safe_row,
                    }
                    records[index] = record
                    checkpoint.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                    checkpoint.flush()
                    os.fsync(checkpoint.fileno())
                submit_available()

    ordered_records = [records[index] for index, _key, _payload in jobs if index in records]
    rows = [record["row"] for record in ordered_records]
    run_status = "paused_quota_exhausted" if quota_error is not None else "completed"
    if quota_error is None and len(records) != len(jobs):
        raise RuntimeError("model job scheduler ended without exact coverage")

    with failures_path.open("w", encoding="utf-8") as failures:
        for record in ordered_records:
            if not record["ok"]:
                failures.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    if quota_error is None:
        with output_path.open("w", encoding="utf-8") as output:
            for row in rows:
                output.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    route_counts = Counter(str(row.get("route_source") or "") for row in rows if row.get("route_source"))
    stats: dict[str, Any] = {
        "run_status": run_status,
        "total": len(jobs),
        "completed": len(records),
        "remaining": len(jobs) - len(records),
        "resumed": resumed_count,
        "processed": len(records) - resumed_count,
        "succeeded": sum(bool(record["ok"]) for record in ordered_records),
        "failed": sum(not record["ok"] for record in ordered_records),
        "route_counts": dict(sorted(route_counts.items())),
    }
    if quota_error is not None:
        stats.update({
            "quota_route": quota_error.route_name,
            "quota_status_code": quota_error.status_code,
            "quota_response_excerpt": _redact_secrets(quota_error.response_excerpt, secrets),
        })
    _write_json_atomic(state_path, stats)
    return rows, stats


def _response_payload(response: Any) -> dict[str, Any]:
    try:
        payload = response.json()
    except Exception as exc:
        raise RuntimeError("model_non_json_response") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("model_response_must_be_object")
    return payload


def _top_level_error(payload: dict[str, Any]) -> str:
    values = []
    for key in ("error", "message", "error_message", "errorMsg"):
        value = payload.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, dict):
            values.extend(str(item) for item in value.values() if isinstance(item, (str, int, float)))
    return " ".join(values)


def _contains_quota_marker(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _QUOTA_MARKERS)


def _extract_knot_content(payload: dict[str, Any]) -> str:
    raw_event = payload.get("rawEvent")
    if isinstance(raw_event, dict) and isinstance(raw_event.get("content"), str):
        return raw_event["content"]
    if isinstance(payload.get("content"), str):
        return payload["content"]
    raise RuntimeError("knot_unknown_response_shape")


def _load_checkpoint(path: Path, expected: dict[int, str]) -> dict[int, dict[str, Any]]:
    if not path.exists():
        return {}
    records: dict[int, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        index = int(record.get("index"))
        key = str(record.get("key") or "")
        if index not in expected or expected[index] != key:
            raise ValueError(f"checkpoint job mismatch at index {index}")
        if record.get("ok") is True:
            records[index] = record
    return records


def _redact_secrets(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        return {
            key: _redact_secrets(item, secrets)
            for key, item in value.items()
            if not any(marker in str(key).lower() for marker in _SECRET_KEY_MARKERS)
        }
    if isinstance(value, list):
        return [_redact_secrets(item, secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_secrets(item, secrets) for item in value)
    if isinstance(value, str):
        redacted = value
        for secret in secrets:
            redacted = redacted.replace(secret, "[REDACTED]")
        return redacted
    return value


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)
