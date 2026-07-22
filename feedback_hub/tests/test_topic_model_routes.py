from __future__ import annotations

import json
import threading
from unittest.mock import Mock

import pytest

from feedback_hub.topic_discovery.model_routes import (
    ModelRoute,
    QuotaExhaustedError,
    call_knot_route,
    run_pauseable_model_jobs,
)


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self) -> dict:
        return self._payload


def _route(name: str = "agent_a") -> ModelRoute:
    return ModelRoute(
        name=name,
        endpoint_class="knot_agent",
        api_url=f"https://{name}.test",
        credential=f"secret-{name}",
    )


def test_call_knot_route_raises_typed_quota_without_retry() -> None:
    post = Mock(return_value=FakeResponse(429, {"error": "daily quota exceeded"}))

    with pytest.raises(QuotaExhaustedError) as caught:
        call_knot_route(
            "prompt",
            route=_route(),
            http_post=post,
            max_retries=4,
            sleep_fn=lambda _seconds: None,
        )

    assert caught.value.route_name == "agent_a"
    assert caught.value.status_code == 429
    assert post.call_count == 1


def test_call_knot_route_classifies_top_level_quota_error() -> None:
    post = Mock(return_value=FakeResponse(403, {"error": "智能体调用次数上限"}))

    with pytest.raises(QuotaExhaustedError):
        call_knot_route(
            "prompt",
            route=_route(),
            http_post=post,
            sleep_fn=lambda _seconds: None,
        )

    assert post.call_count == 1


def test_call_knot_route_retries_transient_error_on_same_route() -> None:
    post = Mock(side_effect=[
        FakeResponse(503, {"error": "busy"}),
        FakeResponse(200, {"rawEvent": {"content": '{"ok":true}'}}),
    ])

    reply = call_knot_route(
        "prompt",
        route=_route(),
        http_post=post,
        max_retries=2,
        sleep_fn=lambda _seconds: None,
    )

    assert reply.content == '{"ok":true}'
    assert reply.route_name == "agent_a"
    assert reply.attempts == 2
    assert reply.retry_chain == ("http_503",)
    assert post.call_count == 2
    assert all(call.kwargs["headers"]["x-knot-api-token"] == "secret-agent_a" for call in post.call_args_list)


def test_call_knot_route_does_not_scan_normal_content_for_quota_words() -> None:
    post = Mock(return_value=FakeResponse(200, {
        "rawEvent": {"content": '{"reason":"用户询问额度"}'},
    }))

    reply = call_knot_route(
        "prompt",
        route=_route(),
        http_post=post,
        sleep_fn=lambda _seconds: None,
    )

    assert reply.content == '{"reason":"用户询问额度"}'


def test_pauseable_scheduler_stops_new_work_after_first_quota(tmp_path) -> None:
    jobs = [(index, f"j{index}", {"value": index}) for index in range(10)]
    seen: list[str] = []
    lock = threading.Lock()

    def worker(index, key, payload, route):
        with lock:
            seen.append(key)
        if key == "j2":
            raise QuotaExhaustedError(route.name, 429, "quota")
        return {"key": key, "route_source": route.name}, None

    output_path = tmp_path / "rows.jsonl"
    rows, stats = run_pauseable_model_jobs(
        jobs,
        worker,
        routes=[_route("agent_a"), _route("agent_b")],
        output_path=output_path,
        concurrency_per_route=1,
    )

    assert stats["run_status"] == "paused_quota_exhausted"
    assert stats["quota_route"] in {"agent_a", "agent_b"}
    assert len(seen) < len(jobs)
    assert len(rows) == stats["completed"]
    assert stats["remaining"] == len(jobs) - len(rows)
    assert not output_path.exists()
    state = json.loads((tmp_path / "rows.jsonl.run_state.json").read_text(encoding="utf-8"))
    assert state["run_status"] == "paused_quota_exhausted"
    assert "secret-agent" not in json.dumps(state)


def test_pauseable_scheduler_resume_skips_validated_checkpoint_rows(tmp_path) -> None:
    jobs = [(index, f"j{index}", {"value": index}) for index in range(3)]
    output_path = tmp_path / "rows.jsonl"
    calls: list[str] = []

    def worker(index, key, payload, route):
        calls.append(key)
        return {
            "key": key,
            "value": payload["value"],
            "route_source": route.name,
        }, None

    first_rows, first_stats = run_pauseable_model_jobs(
        jobs,
        worker,
        routes=[_route()],
        output_path=output_path,
        concurrency_per_route=1,
    )
    calls.clear()
    resumed_rows, resumed_stats = run_pauseable_model_jobs(
        jobs,
        worker,
        routes=[_route()],
        output_path=output_path,
        concurrency_per_route=1,
        resume=True,
    )

    assert calls == []
    assert resumed_rows == first_rows
    assert [row["key"] for row in resumed_rows] == ["j0", "j1", "j2"]
    assert first_stats["run_status"] == "completed"
    assert resumed_stats["run_status"] == "completed"
    assert resumed_stats["resumed"] == 3
    assert len(output_path.read_text(encoding="utf-8").splitlines()) == 3


def test_pauseable_scheduler_resume_preserves_unicode_separator_in_checkpoint_reply(tmp_path) -> None:
    jobs = [(0, "j0", {"value": 0})]
    output_path = tmp_path / "rows.jsonl"
    raw_reply = "第一段\u2028第二段"
    calls: list[str] = []

    def worker(index, key, payload, route):
        calls.append(key)
        return {
            "key": key,
            "raw_reply": raw_reply,
            "route_source": route.name,
        }, None

    first_rows, first_stats = run_pauseable_model_jobs(
        jobs,
        worker,
        routes=[_route()],
        output_path=output_path,
        concurrency_per_route=1,
    )
    calls.clear()

    resumed_rows, resumed_stats = run_pauseable_model_jobs(
        jobs,
        worker,
        routes=[_route()],
        output_path=output_path,
        concurrency_per_route=1,
        resume=True,
    )

    assert first_stats["run_status"] == "completed"
    assert calls == []
    assert resumed_stats["run_status"] == "completed"
    assert resumed_stats["resumed"] == 1
    assert first_rows == resumed_rows
    assert resumed_rows[0]["raw_reply"] == raw_reply


def test_pauseable_scheduler_publishes_each_safe_checkpoint_before_more_work(tmp_path) -> None:
    jobs = [(index, f"j{index}", {"value": index}) for index in range(3)]
    output_path = tmp_path / "rows.jsonl"
    calls: list[str] = []

    def worker(index, key, payload, route):
        calls.append(key)
        return {"key": key, "value": payload["value"]}, None

    def stop_after_first_checkpoint() -> None:
        checkpoint = output_path.with_suffix(
            output_path.suffix + ".checkpoint.jsonl"
        )
        if checkpoint.exists() and len(checkpoint.read_text(encoding="utf-8").splitlines()) == 1:
            raise RuntimeError("simulated_worker_loss")

    with pytest.raises(RuntimeError, match="simulated_worker_loss"):
        run_pauseable_model_jobs(
            jobs,
            worker,
            routes=[_route()],
            output_path=output_path,
            concurrency_per_route=1,
            progress_callback=stop_after_first_checkpoint,
        )

    assert calls == ["j0"]
    calls.clear()
    rows, stats = run_pauseable_model_jobs(
        jobs,
        worker,
        routes=[_route()],
        output_path=output_path,
        concurrency_per_route=1,
        resume=True,
    )

    assert calls == ["j1", "j2"]
    assert [row["key"] for row in rows] == ["j0", "j1", "j2"]
    assert stats["resumed"] == 1


def test_pauseable_scheduler_redacts_route_credentials_from_rows(tmp_path) -> None:
    route = _route()

    def worker(index, key, payload, selected_route):
        return {"error_detail": f"request failed with {selected_route.credential}"}, "failed"

    rows, stats = run_pauseable_model_jobs(
        [(0, "j0", {})],
        worker,
        routes=[route],
        output_path=tmp_path / "rows.jsonl",
        concurrency_per_route=1,
    )

    assert rows[0]["error_detail"] == "request failed with [REDACTED]"
    assert stats["failed"] == 1
    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in tmp_path.iterdir()
        if path.is_file()
    )
    assert route.credential not in persisted
