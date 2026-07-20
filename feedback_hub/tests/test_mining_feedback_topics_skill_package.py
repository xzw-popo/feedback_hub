from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError

import yaml

from feedback_hub.tests.test_topic_mining_contracts import valid_spec
from feedback_hub.topic_mining.contracts import topic_spec_json_schema


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
