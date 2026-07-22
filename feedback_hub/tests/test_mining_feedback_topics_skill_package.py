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


def _platform_contract():
    return {
        "canonical_values": ["Win", "Android", "iOS", "Mac"],
        "aliases": {
            "Win": "Win", "Windows": "Win", "Win端": "Win", "Windows端": "Win",
            "Android": "Android", "安卓": "Android", "Android端": "Android", "安卓端": "Android",
            "iOS": "iOS", "iOS端": "iOS",
            "Mac": "Mac", "macOS": "Mac", "Mac端": "Mac", "macOS端": "Mac",
        },
        "matching": "case_insensitive_ignore_whitespace",
    }


def _run_validator(path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), *arguments, str(path)],
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


def test_skill_documents_platform_canonicalization_contract():
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    topic_spec = (SKILL_ROOT / "references" / "topic-spec.md").read_text(
        encoding="utf-8",
    )
    backend = (SKILL_ROOT / "references" / "backend-contract.md").read_text(
        encoding="utf-8",
    )
    schema = json.loads(
        (SKILL_ROOT / "references" / "topic-spec.schema.json").read_text(
            encoding="utf-8",
        )
    )

    assert "Windows -> Win" in topic_spec
    assert "安卓 -> Android" in topic_spec
    assert "unsupported or ambiguous" in topic_spec
    assert "scope_filters.platforms" in backend
    assert "canonical platform" in skill
    assert schema["properties"]["scope"]["properties"]["platforms"]["items"] == {
        "enum": ["Win", "Android", "iOS", "Mac"],
    }


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


def test_skill_validator_fills_missing_time_pair_from_fixed_now(tmp_path):
    raw = valid_spec()
    raw["scope"].pop("start_time")
    raw["scope"].pop("end_time")
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    result = _run_validator(
        path,
        "--default-now",
        "2026-07-21T12:00:00+08:00",
        "--default-days",
        "14",
    )

    assert result.returncode == 0, result.stderr
    normalized = json.loads(result.stdout)
    assert normalized["scope"]["start_time"] == "2026-07-07T12:00:00+08:00"
    assert normalized["scope"]["end_time"] == "2026-07-21T12:00:00+08:00"


def test_validator_prepares_independent_spec_consumed_by_create_run(tmp_path, monkeypatch):
    raw = valid_spec()
    raw["scope"].pop("start_time")
    raw["scope"].pop("end_time")
    source = tmp_path / "source.json"
    prepared = tmp_path / "prepared.json"
    source.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    result = _run_validator(
        source,
        "--default-now", "2026-07-21T12:00:00+08:00",
        "--default-days", "14",
        "--output", str(prepared),
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(source.read_text(encoding="utf-8"))["scope"].get("start_time") is None
    expected = json.loads(result.stdout)
    assert json.loads(prepared.read_text(encoding="utf-8")) == expected

    module = _load_client_module()
    submitted = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"run_id":"run-1"}'

    def recording_urlopen(request, *, timeout):
        submitted.append(json.loads(request.data.decode("utf-8")))
        return Response()

    monkeypatch.setattr(module.urllib.request, "urlopen", recording_urlopen)
    code, stdout, stderr = _run_client(
        module,
        ["--base-url", "https://topic.internal", "create-run", "--spec", str(prepared)],
    )

    assert code == 0, stderr
    assert json.loads(stdout) == {"run_id": "run-1"}
    assert submitted == [expected]
    assert submitted[0]["scope"]["start_time"] == "2026-07-07T12:00:00+08:00"


def test_validator_failure_leaves_no_prepared_output(tmp_path):
    raw = valid_spec()
    raw["scope"].pop("start_time")
    source = tmp_path / "source.json"
    prepared = tmp_path / "prepared.json"
    source.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    result = _run_validator(source, "--output", str(prepared))

    assert result.returncode == 2
    assert not prepared.exists()
    assert not prepared.with_name(f".{prepared.name}.tmp").exists()


def test_validator_rejects_overwriting_source_with_prepared_output(tmp_path):
    source = tmp_path / "source.json"
    source.write_text(json.dumps(valid_spec(), ensure_ascii=False), encoding="utf-8")
    original = source.read_bytes()

    result = _run_validator(source, "--output", str(source))

    assert result.returncode == 2
    assert "must differ from topic spec path" in result.stderr
    assert source.read_bytes() == original


def test_validator_atomic_output_failure_preserves_target_and_removes_temporary_file(tmp_path, monkeypatch):
    validator = _load_validator_module()
    source = tmp_path / "source.json"
    target = tmp_path / "prepared.json"
    source.write_text("{}", encoding="utf-8")
    target.write_bytes(b"old")

    def fail_replace(_source, _target):
        raise OSError("replace failed")

    monkeypatch.setattr(validator.os, "replace", fail_replace)
    with pytest.raises(ValueError, match="cannot write prepared topic spec"):
        validator._write_prepared_spec(target, source, b"new\n")

    assert target.read_bytes() == b"old"
    assert list(tmp_path.glob(".prepared.json.*.tmp")) == []


def test_skill_validator_rejects_only_one_time_boundary(tmp_path):
    raw = valid_spec()
    raw["scope"].pop("start_time")
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    result = _run_validator(
        path,
        "--default-now",
        "2026-07-21T12:00:00+08:00",
        "--default-days",
        "14",
    )

    assert result.returncode == 2
    assert "both start_time and end_time" in result.stderr


def test_skill_validator_rejects_conversation_unit_before_create_run(tmp_path):
    raw = valid_spec()
    raw["unit"] = "conversation"
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    result = _run_validator(path)

    assert result.returncode == 2
    assert "$.unit: must be one of: feedback" in result.stderr


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


def test_client_prepare_spec_anchors_missing_scope_to_backend_waterline(tmp_path, monkeypatch):
    raw = valid_spec()
    raw["scope"].pop("start_time")
    raw["scope"].pop("end_time")
    raw["scope"]["platforms"] = ["Windows", "WIN"]
    source = tmp_path / "source.json"
    prepared = tmp_path / "prepared.json"
    source.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    module = _load_client_module()
    requests = []

    def request(base_url, token, method, path, payload=None, *, expect_json=True):
        requests.append((method, path))
        return ({
            "default_time_days": 14,
            "source_freshness": {
                "ready": True,
                "available_through": "2026-07-22T16:40:01+08:00",
            },
            "scope_filters": {"platforms": _platform_contract()},
        }, b"")

    monkeypatch.setattr(module, "_request", request)
    code, stdout, stderr = _run_client(module, [
        "prepare-spec", "--spec", str(source), "--output", str(prepared),
        "--window-days", "7",
    ])

    assert code == 0, stderr
    assert requests == [("GET", "/capabilities")]
    normalized = json.loads(stdout)
    assert normalized["scope"]["start_time"] == "2026-07-15T16:40:01+08:00"
    assert normalized["scope"]["end_time"] == "2026-07-22T16:40:01+08:00"
    assert normalized["scope"]["platforms"] == ["Win"]
    assert json.loads(prepared.read_text(encoding="utf-8")) == normalized
    assert "start_time" not in json.loads(source.read_text(encoding="utf-8"))["scope"]


def test_client_prepare_spec_preserves_complete_explicit_time_pair(tmp_path, monkeypatch):
    source = tmp_path / "source.json"
    prepared = tmp_path / "prepared.json"
    source.write_text(json.dumps(valid_spec(), ensure_ascii=False), encoding="utf-8")
    module = _load_client_module()
    monkeypatch.setattr(module, "_request", lambda *args, **kwargs: ({
        "default_time_days": 14,
        "source_freshness": {
            "ready": True,
            "available_through": "2026-07-22T16:40:01+08:00",
        },
        "scope_filters": {"platforms": _platform_contract()},
    }, b""))

    code, stdout, stderr = _run_client(module, [
        "prepare-spec", "--spec", str(source), "--output", str(prepared),
    ])

    assert code == 0, stderr
    assert json.loads(stdout)["scope"] == valid_spec()["scope"]


@pytest.mark.parametrize(
    ("alias", "canonical"), [("Windows", "Win"), ("安卓", "Android")],
)
def test_validator_requires_contract_to_prepare_platform_alias(
    tmp_path, alias, canonical,
):
    raw = valid_spec()
    raw["scope"]["platforms"] = [alias]
    source = tmp_path / "source.json"
    source.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    without_contract = _run_validator(source)
    with_contract = _run_validator(
        source,
        "--platform-contract-json", json.dumps(_platform_contract(), ensure_ascii=False),
    )

    assert without_contract.returncode == 2
    assert "scope.platforms" in without_contract.stderr
    assert with_contract.returncode == 0, with_contract.stderr
    assert json.loads(with_contract.stdout)["scope"]["platforms"] == [canonical]


@pytest.mark.parametrize(
    "platform_contract",
    [
        None,
        {"canonical_values": ["Win"], "aliases": {"Win": "Win"}, "matching": "case_insensitive_ignore_whitespace"},
        {"canonical_values": ["Win", "Android", "iOS", "Mac"], "aliases": {"Win": "Linux"}, "matching": "case_insensitive_ignore_whitespace"},
    ],
)
def test_client_prepare_spec_fails_closed_on_missing_or_malformed_platform_contract(
    tmp_path, monkeypatch, platform_contract,
):
    source = tmp_path / "source.json"
    prepared = tmp_path / "prepared.json"
    source.write_text(json.dumps(valid_spec(), ensure_ascii=False), encoding="utf-8")
    prepared.write_text("old", encoding="utf-8")
    capabilities = {
        "default_time_days": 14,
        "source_freshness": {
            "ready": True,
            "available_through": "2026-07-22T16:40:01+08:00",
        },
    }
    if platform_contract is not None:
        capabilities["scope_filters"] = {"platforms": platform_contract}
    module = _load_client_module()
    monkeypatch.setattr(
        module, "_request", lambda *_args, **_kwargs: (capabilities, b""),
    )

    code, _, stderr = _run_client(module, [
        "prepare-spec", "--spec", str(source), "--output", str(prepared),
    ])

    assert code == 2
    assert "platform" in stderr.lower()
    assert "Traceback" not in stderr
    assert prepared.read_text(encoding="utf-8") == "old"


def test_client_prepare_spec_rejects_unready_source_without_output(tmp_path, monkeypatch):
    source = tmp_path / "source.json"
    prepared = tmp_path / "prepared.json"
    source.write_text(json.dumps(valid_spec(), ensure_ascii=False), encoding="utf-8")
    module = _load_client_module()
    monkeypatch.setattr(module, "_request", lambda *args, **kwargs: ({
        "default_time_days": 14,
        "source_freshness": {"ready": False, "available_through": None},
    }, b""))

    code, _, stderr = _run_client(module, [
        "prepare-spec", "--spec", str(source), "--output", str(prepared),
    ])

    assert code == 2
    assert "source freshness is not ready" in stderr
    assert not prepared.exists()


def test_client_uses_packaged_default_url_without_configuration(monkeypatch):
    monkeypatch.delenv("FEEDBACK_TOPIC_API_URL", raising=False)
    module = _load_client_module()
    requested = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"authentication":"internal_network_boundary"}'

    def recording_urlopen(request, *, timeout):
        requested.append((request.full_url, timeout))
        return Response()

    monkeypatch.setattr(module.urllib.request, "urlopen", recording_urlopen)
    code, stdout, stderr = _run_client(module, ["capabilities"])

    assert code == 0, stderr
    assert json.loads(stdout) == {"authentication": "internal_network_boundary"}
    assert requested == [(
        "http://charvelxia-any2.devcloud.woa.com:8000/api/topic-mining/capabilities",
        30,
    )]


def test_client_whitespace_environment_url_uses_packaged_default(monkeypatch):
    monkeypatch.setenv("FEEDBACK_TOPIC_API_URL", "   \t")
    module = _load_client_module()
    requested = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{}'

    def recording_urlopen(request, *, timeout):
        requested.append(request.full_url)
        return Response()

    monkeypatch.setattr(module.urllib.request, "urlopen", recording_urlopen)
    code, _, stderr = _run_client(module, ["capabilities"])

    assert code == 0, stderr
    assert requested == [
        "http://charvelxia-any2.devcloud.woa.com:8000/api/topic-mining/capabilities",
    ]


def test_client_rejects_nonempty_malformed_environment_url_without_request(monkeypatch):
    monkeypatch.setenv("FEEDBACK_TOPIC_API_URL", "not-a-url")
    module = _load_client_module()
    requested = []
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *args, **kwargs: requested.append(args))

    code, _, stderr = _run_client(module, ["capabilities"])

    assert code == 2
    assert "absolute HTTP(S) URL" in stderr
    assert not requested


def test_client_cli_url_takes_precedence_over_environment(monkeypatch):
    monkeypatch.setenv("FEEDBACK_TOPIC_API_URL", "https://environment.internal")
    module = _load_client_module()
    requested = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{}'

    def recording_urlopen(request, *, timeout):
        requested.append(request.full_url)
        return Response()

    monkeypatch.setattr(module.urllib.request, "urlopen", recording_urlopen)
    code, _, stderr = _run_client(
        module, ["--base-url", "https://command.internal", "capabilities"],
    )

    assert code == 0, stderr
    assert requested == ["https://command.internal/api/topic-mining/capabilities"]


def test_client_rejects_explicit_blank_url_instead_of_falling_back(monkeypatch):
    monkeypatch.setenv("FEEDBACK_TOPIC_API_URL", "https://environment.internal")
    module = _load_client_module()
    requested = []
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *args, **kwargs: requested.append(args))

    code, _, stderr = _run_client(module, ["--base-url", "   ", "capabilities"])

    assert code == 2
    assert "base URL must not be blank" in stderr
    assert not requested


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


def test_skill_uses_backend_owned_dynamic_budgets_and_uncapped_confirmed_exports():
    body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2].lower()
    backend = (SKILL_ROOT / "references" / "backend-contract.md").read_text(encoding="utf-8").lower()

    assert "candidate tuning remains backend-owned" in body
    assert "all verified matched rows" in backend
    assert "result_limit=null" in backend
    for value in ("minimum 100", "80 per effective day", "maximum 500"):
        assert value in backend
    assert "standard classification is always 500" not in body
    assert "export at 100" not in body


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
    assert "when exactly one boundary is supplied or timezone is unknowable" in body
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
        "`prepare-spec --spec topic_spec_path --output prepared_spec_path`",
        "`create-run --spec prepared_spec_path`",
        "`get-run run_id`",
        "`resume run_id`",
        "`review-queue run_id --output file --offset offset --limit 50`",
        "`apply-overrides run_id --file file`",
        "`verify run_id`",
        "`export run_id --format xlsx|jsonl`",
        "`download run_id artifact --output file`",
    )
    positions = [body.index(command) for command in commands]
    assert positions == sorted(positions)


def test_skill_guidance_asks_for_one_sided_or_timezone_missing_time_as_a_direct_question():
    body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2].lower()
    assert "what start time, end time, and timezone should this run use?" in body


def test_skill_defaults_missing_time_to_backend_advertised_two_weeks():
    body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2].lower()
    assert "default to the most recent 14 days" in body
    assert "ask the single time-range question" not in body


def test_skill_uses_exhaustive_only_for_explicit_completeness_intent():
    body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2].lower()
    assert "all, complete, or exhaustive" in body
    assert "mode: exhaustive" in body
    assert "mode: standard" in body


def test_skill_and_references_advertise_only_feedback_units():
    body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2].lower()
    topic_spec = (SKILL_ROOT / "references" / "topic-spec.md").read_text(encoding="utf-8").lower()
    backend = (SKILL_ROOT / "references" / "backend-contract.md").read_text(encoding="utf-8").lower()
    schema = json.loads((SKILL_ROOT / "references" / "topic-spec.schema.json").read_text(encoding="utf-8"))

    assert "`unit: feedback`" in body
    assert "only `feedback` is supported" in topic_spec
    assert "supported_units=[\"feedback\"]" in backend
    assert schema["properties"]["unit"] == {"enum": ["feedback"]}


def test_skill_exhausts_review_pagination_before_submitting_one_override_set():
    body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2].lower()
    assert "limit 50" in body
    assert "until `next_offset` is null" in body
    assert "merge all page decisions" in body
    assert body.index("until `next_offset` is null") < body.index("`apply-overrides run_id --file file`")


def test_skill_discloses_representative_scope():
    body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2].lower()
    assert "result_scope" in body
    assert "returned_feedback" in body
    assert "possibly_more_matches" in body
    assert "representative" in body


def test_skill_delivery_copies_all_five_backend_scope_fields_only():
    body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2].lower()
    for field in (
        "mode", "result_scope", "matched_total", "returned_feedback",
        "possibly_more_matches",
    ):
        assert f"`{field}`" in body
    assert "copy only these five backend-returned fields" in body


def test_skill_uses_only_backend_returned_result_scope_values():
    body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2].lower()
    assert "result_scope=reviewed" in body
    assert "never invent a result_scope value" in body


def test_skill_current_internal_deployment_uses_default_with_optional_overrides():
    body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2]
    assert "FEEDBACK_TOPIC_API_URL" in body
    assert "FEEDBACK_TOPIC_API_TOKEN" in body
    assert "packaged internal default" in body
    assert "optional override" in body
    assert "Require `FEEDBACK_TOPIC_API_URL`" not in body
    assert "unused for the current internal deployment" in body

    backend = (SKILL_ROOT / "references" / "backend-contract.md").read_text(encoding="utf-8")
    assert "http://charvelxia-any2.devcloud.woa.com:8000" in backend
    assert "Both URL overrides are optional" in backend
    assert "Configure `FEEDBACK_TOPIC_API_URL`" not in backend


def test_skill_guidance_resolves_explicit_relative_time_without_questioning():
    body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2].lower()
    assert "treat an explicit relative time range as supplied scope" in body
    assert "resolve whole-day relative ranges from `available_through`" in body
    assert "system or local current time" in body


def test_skill_anchors_default_and_relative_windows_to_backend_waterline():
    body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2].lower()
    assert "source_freshness.available_through" in body
    assert "`prepare-spec --spec topic_spec_path --output prepared_spec_path`" in body
    assert "`--window-days days`" in body
    assert "never use the agent's system or local current time" in body


def test_backend_contract_has_one_copyable_complete_command_sequence():
    text = (SKILL_ROOT / "references" / "backend-contract.md").read_text(encoding="utf-8")
    commands = (
        "python3 scripts/topic_backend_client.py capabilities",
        "python3 scripts/topic_backend_client.py prepare-spec --spec TOPIC_SPEC_PATH --output PREPARED_SPEC_PATH",
        "python3 scripts/topic_backend_client.py create-run --spec PREPARED_SPEC_PATH",
        "python3 scripts/topic_backend_client.py get-run RUN_ID",
        "python3 scripts/topic_backend_client.py resume RUN_ID",
        "python3 scripts/topic_backend_client.py review-queue RUN_ID --output REVIEW_PAGE_PATH --offset OFFSET --limit 50",
        "python3 scripts/topic_backend_client.py apply-overrides RUN_ID --file OVERRIDES_PATH",
        "python3 scripts/topic_backend_client.py verify RUN_ID",
        "python3 scripts/topic_backend_client.py export RUN_ID --format xlsx",
        "python3 scripts/topic_backend_client.py download RUN_ID ARTIFACT_NAME --output OUTPUT_PATH",
    )
    positions = [text.index(command) for command in commands]
    assert positions == sorted(positions)


def test_skill_references_define_default_mode_paging_and_delivery_policy():
    topic_spec = (SKILL_ROOT / "references" / "topic-spec.md").read_text(encoding="utf-8")
    backend = (SKILL_ROOT / "references" / "backend-contract.md").read_text(encoding="utf-8")
    review = (SKILL_ROOT / "references" / "review-policy.md").read_text(encoding="utf-8")

    assert "`mode: standard`" in topic_spec
    assert "`mode: exhaustive`" in topic_spec
    assert "prepare-spec --spec TOPIC_SPEC_PATH --output PREPARED_SPEC_PATH" in topic_spec
    for value in ("minimum 100", "80 per effective day", "maximum 500", "result_scope=representative", "possibly_more_matches=true"):
        assert value in backend
    assert "authentication=internal_network_boundary" in backend
    for field in ("mode", "result_scope", "matched_total", "returned_feedback", "possibly_more_matches"):
        assert f"`{field}`" in backend
    assert "follow the returned `next_offset` and stop only when it is null" in backend
    assert "including queues of 51 or more items" in review
    assert "call `apply-overrides` only once" in review
    assert "retrieved_candidate_count > classified_count" in backend
    assert "possibly_more_matches=true in either mode" in backend


def test_references_use_backend_anchored_atomic_time_preparation_command():
    for name in ("topic-spec.md", "backend-contract.md"):
        text = (SKILL_ROOT / "references" / name).read_text(encoding="utf-8")
        commands = [
            line for line in text.splitlines()
            if "python3 scripts/topic_backend_client.py prepare-spec" in line
        ]
        assert commands, name
        assert all("--output PREPARED_SPEC_PATH" in command for command in commands), (name, commands)
        assert "--default-now NOW" not in text


def test_review_contract_requires_complete_context_grounded_decisions():
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    contract = (
        SKILL_ROOT / "references" / "backend-contract.md"
    ).read_text(encoding="utf-8")
    policy = (
        SKILL_ROOT / "references" / "review-policy.md"
    ).read_text(encoding="utf-8")

    assert "one explicit decision for every item" in contract
    assert "context_items" in skill
    assert "context_items" in policy
    assert "non-empty string list" in policy
    assert "exact substring" in policy


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
        [
            "review-queue", "run-1", "--output", str(review_output),
            "--offset", "7", "--limit", "20",
        ],
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
        ("GET", "https://topic.internal/api/topic-mining/runs/run-1/review-queue?offset=7&limit=20", None, 30),
        ("POST", "https://topic.internal/api/topic-mining/runs/run-1/overrides", b"[]", 30),
        ("POST", "https://topic.internal/api/topic-mining/runs/run-1/verify", None, 30),
        ("POST", "https://topic.internal/api/topic-mining/runs/run-1/export", b'{"format":"xlsx"}', 30),
        ("GET", "https://topic.internal/api/topic-mining/runs/run-1/artifacts/results.xlsx", None, 30),
    ]
    assert json.loads(review_output.read_text(encoding="utf-8")) == {"ok": True}
    assert download_output.read_bytes() == b"binary-xlsx"


@pytest.mark.parametrize(
    "arguments",
    (
        ["--offset", "-1"],
        ["--limit", "0"],
        ["--limit", "51"],
        ["--offset", "not-an-integer"],
    ),
)
def test_client_rejects_invalid_review_page_arguments_before_request(
    tmp_path, monkeypatch, arguments,
):
    module = _load_client_module()
    requested = []
    monkeypatch.setattr(
        module.urllib.request, "urlopen",
        lambda *args, **kwargs: requested.append(args),
    )

    code, _, stderr = _run_client(
        module,
        [
            "--base-url", "https://topic.internal", "review-queue", "run-1",
            "--output", str(tmp_path / "page.json"), *arguments,
        ],
    )

    assert code == 2
    assert "review" in stderr
    assert not requested
