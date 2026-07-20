from __future__ import annotations

import contextlib
import http.client
import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError

import pytest
import yaml

from feedback_hub.tests.test_topic_mining_contracts import valid_spec
from feedback_hub.topic_mining.contracts import topic_spec_json_schema, validate_topic_spec


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = REPO_ROOT / "codex-skills" / "mining-feedback-topics"
VALIDATOR = SKILL_ROOT / "scripts" / "validate_topic_spec.py"
CLIENT = SKILL_ROOT / "scripts" / "topic_backend_client.py"


def _run_validator(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), str(path)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _load_client_module():
    spec = importlib.util.spec_from_file_location("topic_backend_client", CLIENT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_validator_module():
    spec = importlib.util.spec_from_file_location("validate_topic_spec", VALIDATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_client(module, arguments: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            module.main(arguments)
        except SystemExit as error:
            return int(error.code), stdout.getvalue(), stderr.getvalue()
    return 0, stdout.getvalue(), stderr.getvalue()


def _parse_frontmatter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    _, metadata, _ = text.split("---", 2)
    value = yaml.safe_load(metadata)
    assert isinstance(value, dict)
    return value


def test_skill_schema_matches_backend_contract():
    bundled = json.loads((SKILL_ROOT / "references/topic-spec.schema.json").read_text(encoding="utf-8"))
    assert bundled == topic_spec_json_schema()


def test_validator_accepts_valid_json_and_rejects_bottom_layer_parameter(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(valid_spec(), ensure_ascii=False), encoding="utf-8")
    result = _run_validator(good)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == valid_spec()

    bad = valid_spec()
    bad["top_k"] = 100
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    result = _run_validator(bad_path)
    assert result.returncode == 2
    assert "unknown field: top_k" in result.stderr


def test_validator_accepts_yaml_and_requires_timezone(tmp_path):
    bad = valid_spec()
    bad["scope"]["start_time"] = "2026-01-16T00:00:00"
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(bad, allow_unicode=True), encoding="utf-8")

    result = _run_validator(path)

    assert result.returncode == 2
    assert "$.scope.start_time: timezone is required" in result.stderr


def test_client_uses_environment_url_and_redacts_token(monkeypatch):
    monkeypatch.setenv("FEEDBACK_TOPIC_API_URL", "https://topic.internal")
    monkeypatch.setenv("FEEDBACK_TOPIC_API_TOKEN", "secret-value")
    module = _load_client_module()

    requested = []

    def raising_urlopen(request, *, timeout):
        requested.append((request.full_url, request.get_header("Authorization"), timeout))
        raise URLError("failed with secret-value")

    monkeypatch.setattr(module.urllib.request, "urlopen", raising_urlopen)
    code, _, stderr = _run_client(module, ["capabilities"])

    assert code == 4
    assert requested == [("https://topic.internal/api/topic-mining/capabilities", "Bearer secret-value", 30)]
    assert "secret-value" not in stderr
    assert "[REDACTED]" in stderr


def test_skill_frontmatter_has_only_name_and_description():
    metadata = _parse_frontmatter(SKILL_ROOT / "SKILL.md")
    assert set(metadata) == {"name", "description"}
    assert metadata["name"] == "mining-feedback-topics"
    assert isinstance(metadata["description"], str)
    assert metadata["description"].startswith("Use when")


def test_skill_guidance_has_auditable_ordered_workflow_and_direct_resources():
    text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    body = text.split("---", 2)[2]
    assert len(body.split()) < 500
    for resource in (
        "[topic spec](references/topic-spec.md)",
        "[backend contract](references/backend-contract.md)",
        "[review policy](references/review-policy.md)",
        "[validate_topic_spec.py](scripts/validate_topic_spec.py)",
        "[topic_backend_client.py](scripts/topic_backend_client.py)",
    ):
        assert resource in body
        assert f"`{resource}" not in body
    gates = ("capabilities", "validate", "create", "inspect", "review", "verify", "export", "deliver")
    positions = [body.lower().index(gate) for gate in gates]
    assert positions == sorted(positions)
    for condition in (
        "data coverage",
        "vector watermark",
        "exact classification coverage",
        "source evidence",
        "valid links",
    ):
        assert condition in body.lower()


def test_skill_guidance_does_not_embed_topic_rules_or_backend_tuning():
    text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    body = text.split("---", 2)[2].lower()
    forbidden = (
        "左下角",
        "右上角",
        "163 条",
        "147 条",
        "310 条",
        "select ",
        "top-k",
        "similarity threshold",
        "api_key",
        "bearer ",
        "https://",
    )
    for value in forbidden:
        assert value not in body


def test_skill_guidance_keeps_unverified_or_plan_only_work_inside_the_backend_contract():
    body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2].lower()
    assert "do not query source databases or vector tools directly" in body
    assert "do not claim artifacts, results, or validation" in body
    assert "do not expand the user's named object into a different object or product" in body


def test_skill_guidance_preserves_user_named_target_objects():
    body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2].lower()
    assert "keep each user-named target object and behavior as required inclusion conditions" in body
    assert "put unrequested adjacent objects or behaviors only in exclusion criteria" in body


def test_skill_guidance_copies_hard_scope_and_client_commands_without_invention():
    body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2].lower()
    assert "populate each hard-scope field only with values explicit in the current request" in body
    assert "leave every other hard-scope field empty" in body
    assert "copy client commands from the backend contract verbatim" in body


def test_skill_guidance_shapes_blocked_pre_run_responses():
    body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2].lower()
    assert "a blocked pre-run response contains four slots" in body
    for slot in ("proposed inclusion", "proposed exclusion", "one material scope question", "service or configuration blocker"):
        assert slot in body
    assert "if start or end time is missing, ask the single time-range question" in body
    assert "never default to all history" in body


def test_skill_guidance_assigns_capability_and_run_quality_fields_to_the_right_commands():
    body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2].lower()
    capabilities = next(line for line in body.splitlines() if line.startswith("1. **capabilities."))
    inspect = next(line for line in body.splitlines() if line.startswith("4. **inspect."))
    for value in ("schema versions", "formats", "statuses", "read-only flags"):
        assert value in capabilities
    assert "vector watermark" not in capabilities
    assert "`get-run run_id`" in inspect
    assert "vector watermark" in inspect


def test_skill_guidance_presents_the_complete_verbatim_client_sequence():
    body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2].lower()
    commands = (
        "`capabilities`",
        "validate_topic_spec.py",
        "`create-run --spec file`",
        "`get-run run_id`",
        "`resume run_id`",
        "`review-queue run_id --output file`",
        "`apply-overrides run_id --file file`",
        "`verify run_id`",
        "`export run_id --format xlsx|jsonl`",
        "`download run_id artifact --output file`",
    )
    positions = [body.index(command) for command in commands]
    assert positions == sorted(positions)


def test_skill_guidance_asks_for_missing_time_as_a_direct_question():
    body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2].lower()
    assert "what start time, end time, and timezone should this run use?" in body


def test_skill_guidance_resolves_explicit_relative_time_without_questioning():
    body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2].lower()
    assert "treat an explicit relative time range as supplied scope" in body
    assert "resolve it from the current date and timezone without asking again" in body


def test_backend_contract_has_one_copyable_complete_command_sequence():
    text = (SKILL_ROOT / "references" / "backend-contract.md").read_text(encoding="utf-8")
    commands = (
        "python3 scripts/topic_backend_client.py capabilities",
        "python3 scripts/validate_topic_spec.py TOPIC_SPEC_PATH",
        "python3 scripts/topic_backend_client.py create-run --spec TOPIC_SPEC_PATH",
        "python3 scripts/topic_backend_client.py get-run RUN_ID",
        "python3 scripts/topic_backend_client.py resume RUN_ID",
        "python3 scripts/topic_backend_client.py review-queue RUN_ID --output REVIEW_PATH",
        "python3 scripts/topic_backend_client.py apply-overrides RUN_ID --file OVERRIDES_PATH",
        "python3 scripts/topic_backend_client.py verify RUN_ID",
        "python3 scripts/topic_backend_client.py export RUN_ID --format xlsx",
        "python3 scripts/topic_backend_client.py download RUN_ID ARTIFACT_NAME --output OUTPUT_PATH",
    )
    positions = [text.index(command) for command in commands]
    assert positions == sorted(positions)


def test_skill_requires_repair_then_resume_of_the_original_recoverable_run():
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8").lower()
    contract = (
        SKILL_ROOT / "references" / "backend-contract.md"
    ).read_text(encoding="utf-8").lower()

    for text in (skill, contract):
        assert "paused_quota_exhausted" in text
        assert "failed" in text
        assert "resume run_id" in text
        assert "original run" in text
        assert "do not create a replacement run" in text


def test_backend_contract_keeps_internal_artifacts_out_of_download_contract():
    contract = (
        SKILL_ROOT / "references" / "backend-contract.md"
    ).read_text(encoding="utf-8").lower()

    assert "backend-internal" in contract
    assert "never available through artifact download" in contract
    assert "only verified final deliverables" in contract
    for name in (
        "final_results.jsonl", "quality_report.json", "feedback_list.xlsx",
    ):
        assert name in contract
    assert "the run may expose `source_snapshot.sqlite`" not in contract


def test_client_download_uses_atomic_replace(tmp_path, monkeypatch):
    module = _load_client_module()
    target = tmp_path / "result.xlsx"
    target.write_bytes(b"old")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"new-artifact"

    monkeypatch.setattr(module.urllib.request, "urlopen", lambda request, *, timeout: Response())
    code, stdout, stderr = _run_client(
        module,
        ["--base-url", "https://topic.internal", "download", "run-1", "result.xlsx", "--output", str(target)],
    )

    assert code == 0, stderr
    assert target.read_bytes() == b"new-artifact"
    assert not target.with_name(f".{target.name}.tmp").exists()
    assert json.loads(stdout) == {"output": str(target), "run_id": "run-1", "artifact_name": "result.xlsx"}


def test_validator_rejects_boolean_schema_version(tmp_path):
    bad = valid_spec()
    bad["schema_version"] = True
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")

    result = _run_validator(path)

    assert result.returncode == 2
    assert "$.schema_version" in result.stderr


@pytest.mark.parametrize(
    "timestamp",
    ["2026-01-16T00:00:00Z", "2026-01-16t00:00:00z"],
)
def test_standalone_and_backend_accept_rfc3339_utc_timestamps(tmp_path, timestamp):
    raw = valid_spec()
    raw["scope"]["start_time"] = timestamp
    raw["scope"]["end_time"] = "2026-07-16T14:00:00Z"
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert _run_validator(path).returncode == 0
    assert validate_topic_spec(raw).scope.start_time.isoformat() == "2026-01-16T00:00:00+00:00"


@pytest.mark.parametrize(
    "timestamp",
    ["2026-01-16 00:00:00+00:00", "2026-01-16T00:00+00:00", "2026-01-16T00:00:00"],
)
def test_standalone_and_backend_reject_non_rfc3339_timestamps(tmp_path, timestamp):
    raw = valid_spec()
    raw["scope"]["start_time"] = timestamp
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    result = _run_validator(path)
    assert result.returncode == 2
    with pytest.raises(ValueError, match="scope.start_time"):
        validate_topic_spec(raw)


@pytest.mark.parametrize(
    "timestamp",
    [" 2026-01-16T00:00:00Z", "2026-01-16T00:00:00Z ", "   "],
)
def test_standalone_and_backend_reject_timestamp_whitespace(tmp_path, timestamp):
    raw = valid_spec()
    raw["scope"]["start_time"] = timestamp
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert _run_validator(path).returncode == 2
    with pytest.raises(ValueError, match="scope.start_time"):
        validate_topic_spec(raw)


@pytest.mark.parametrize("timestamp", ["2026-01-16T00:00:00+00:60", "2026-01-16T00:00:00+24:00"])
def test_standalone_and_backend_reject_invalid_rfc3339_offset_bounds(tmp_path, timestamp):
    raw = valid_spec()
    raw["scope"]["start_time"] = timestamp
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert _run_validator(path).returncode == 2
    with pytest.raises(ValueError, match="scope.start_time"):
        validate_topic_spec(raw)


@pytest.mark.parametrize("timestamp", ["2026-01-16T00:00:00+23:59", "2026-01-16T00:00:00-00:00"])
def test_standalone_and_backend_accept_valid_rfc3339_offset_bounds(tmp_path, timestamp):
    raw = valid_spec()
    raw["scope"]["start_time"] = timestamp
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert _run_validator(path).returncode == 0
    assert validate_topic_spec(raw).scope.start_time.tzinfo is not None


@pytest.mark.parametrize(
    "start_time,end_time",
    [
        ("2026-01-16T00:00:00Z", "2026-01-15T23:59:59Z"),
        ("2026-01-16T00:00:00Z", "2026-01-16T00:00:00Z"),
        ("2026-01-16T00:00:00Z", "2026-01-16T01:00:00+01:00"),
    ],
)
def test_standalone_and_backend_reject_non_increasing_scope_instants(tmp_path, start_time, end_time):
    raw = valid_spec()
    raw["scope"]["start_time"] = start_time
    raw["scope"]["end_time"] = end_time
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    result = _run_validator(path)
    assert result.returncode == 2
    assert "$.scope.end_time" in result.stderr
    with pytest.raises(ValueError, match="scope.end_time"):
        validate_topic_spec(raw)


def test_standalone_and_backend_compare_different_scope_offsets_as_instants(tmp_path):
    raw = valid_spec()
    raw["scope"]["start_time"] = "2026-01-16T00:30:00+01:00"
    raw["scope"]["end_time"] = "2026-01-16T00:00:00Z"
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert _run_validator(path).returncode == 0
    assert validate_topic_spec(raw).scope.start_time < validate_topic_spec(raw).scope.end_time


def test_validator_enum_comparison_does_not_treat_boolean_as_number():
    validator = _load_validator_module()

    with pytest.raises(ValueError, match="must be one of"):
        validator._validate(True, {"enum": [1]}, "$")


@pytest.mark.parametrize("unsafe", ["..", "a/b", r"a\\b", "run?other", "run#fragment", "%2F", "run\x00id"])
def test_client_rejects_unsafe_run_ids_before_building_a_url(monkeypatch, unsafe):
    module = _load_client_module()
    requested = []
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *args, **kwargs: requested.append(args))

    code, _, stderr = _run_client(module, ["--base-url", "https://topic.internal", "get-run", unsafe])

    assert code == 2
    assert "run id" in stderr
    assert not requested


def test_client_resume_uses_token_post_route_and_redacts_http_error(monkeypatch):
    token = "resume-secret"
    monkeypatch.setenv("FEEDBACK_TOPIC_API_TOKEN", token)
    module = _load_client_module()
    requested = []

    def raising_urlopen(request, *, timeout):
        requested.append(
            (request.get_method(), request.full_url, request.get_header("Authorization"), timeout)
        )
        raise module.urllib.error.HTTPError(
            request.full_url,
            409,
            "conflict",
            {},
            io.BytesIO(f'{{"detail":"failed {token}"}}'.encode()),
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", raising_urlopen)
    code, _, stderr = _run_client(
        module, ["--base-url", "https://topic.internal", "resume", "run-1"],
    )

    assert code == 3
    assert requested == [(
        "POST",
        "https://topic.internal/api/topic-mining/runs/run-1/resume",
        f"Bearer {token}",
        30,
    )]
    assert token not in stderr
    assert "[REDACTED]" in stderr


@pytest.mark.parametrize("unsafe", ["..", "a/b", r"a\\b", "file?x", "file#x", "%2F", "file\x1f.xlsx"])
def test_client_rejects_unsafe_artifact_names_before_building_a_url(tmp_path, monkeypatch, unsafe):
    module = _load_client_module()
    requested = []
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *args, **kwargs: requested.append(args))

    code, _, stderr = _run_client(
        module,
        ["--base-url", "https://topic.internal", "download", "run-1", unsafe, "--output", str(tmp_path / "out")],
    )

    assert code == 2
    assert "artifact name" in stderr
    assert not requested


def test_client_maps_invalid_url_and_http_client_exceptions_without_token_leaks(monkeypatch):
    token = "secret-value"
    monkeypatch.setenv("FEEDBACK_TOPIC_API_TOKEN", token)
    module = _load_client_module()

    code, _, stderr = _run_client(module, ["--base-url", "not-a-url", "capabilities"])
    assert code == 2
    assert token not in stderr
    assert "Traceback" not in stderr

    code, _, stderr = _run_client(module, ["--base-url", "https://[", "capabilities"])
    assert code == 2
    assert token not in stderr
    assert "Traceback" not in stderr

    def invalid_url(request, *, timeout):
        raise http.client.InvalidURL(f"invalid route with {token}")

    monkeypatch.setattr(module.urllib.request, "urlopen", invalid_url)
    code, _, stderr = _run_client(module, ["--base-url", "https://topic.internal", "capabilities"])
    assert code == 4
    assert token not in stderr
    assert "[REDACTED]" in stderr
    assert "Traceback" not in stderr


def test_client_argument_errors_are_redacted(monkeypatch):
    token = "secret-value"
    monkeypatch.setenv("FEEDBACK_TOPIC_API_TOKEN", token)
    module = _load_client_module()

    code, _, stderr = _run_client(module, ["--base-url", "https://topic.internal", f"invalid-{token}"])

    assert code == 2
    assert token not in stderr
    assert "[REDACTED]" in stderr
    assert "Traceback" not in stderr


def test_client_rejects_non_json_api_response_and_preserves_review_output(tmp_path, monkeypatch):
    module = _load_client_module()
    target = tmp_path / "review.json"
    target.write_text("old", encoding="utf-8")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"not-json"

    monkeypatch.setattr(module.urllib.request, "urlopen", lambda request, *, timeout: Response())
    code, _, stderr = _run_client(
        module,
        ["--base-url", "https://topic.internal", "review-queue", "run-1", "--output", str(target)],
    )

    assert code == 4
    assert "invalid JSON response" in stderr
    assert target.read_text(encoding="utf-8") == "old"


def test_client_maps_every_command_to_the_contract_route_and_body(tmp_path, monkeypatch):
    module = _load_client_module()
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(valid_spec()), encoding="utf-8")
    overrides_path = tmp_path / "overrides.json"
    overrides_path.write_text("[]", encoding="utf-8")
    review_output = tmp_path / "review.json"
    download_output = tmp_path / "results.xlsx"
    requests = []

    class Response:
        def __init__(self, raw):
            self.raw = raw

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return self.raw

    def urlopen(request, *, timeout):
        requests.append((request.get_method(), request.full_url, request.data, timeout))
        raw = b"binary-xlsx" if "/artifacts/" in request.full_url else b'{"ok":true}'
        return Response(raw)

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    commands = [
        ["capabilities"],
        ["create-run", "--spec", str(spec_path)],
        ["get-run", "run-1"],
        ["resume", "run-1"],
        ["review-queue", "run-1", "--output", str(review_output)],
        ["apply-overrides", "run-1", "--file", str(overrides_path)],
        ["verify", "run-1"],
        ["export", "run-1", "--format", "xlsx"],
        ["download", "run-1", "results.xlsx", "--output", str(download_output)],
    ]
    for command in commands:
        code, _, stderr = _run_client(module, ["--base-url", "https://topic.internal", *command])
        assert code == 0, stderr

    assert [(method, url, body, timeout) for method, url, body, timeout in requests] == [
        ("GET", "https://topic.internal/api/topic-mining/capabilities", None, 30),
        ("POST", "https://topic.internal/api/topic-mining/runs", json.dumps(valid_spec(), ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 30),
        ("GET", "https://topic.internal/api/topic-mining/runs/run-1", None, 30),
        ("POST", "https://topic.internal/api/topic-mining/runs/run-1/resume", None, 30),
        ("GET", "https://topic.internal/api/topic-mining/runs/run-1/review-queue", None, 30),
        ("POST", "https://topic.internal/api/topic-mining/runs/run-1/overrides", b"[]", 30),
        ("POST", "https://topic.internal/api/topic-mining/runs/run-1/verify", None, 30),
        ("POST", "https://topic.internal/api/topic-mining/runs/run-1/export", b'{"format":"xlsx"}', 30),
        ("GET", "https://topic.internal/api/topic-mining/runs/run-1/artifacts/results.xlsx", None, 30),
    ]
    assert json.loads(review_output.read_text(encoding="utf-8")) == {"ok": True}
    assert download_output.read_bytes() == b"binary-xlsx"
