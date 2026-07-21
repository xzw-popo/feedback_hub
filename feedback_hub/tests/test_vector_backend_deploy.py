from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
VECTOR_DEPLOY = REPO_ROOT / "scripts" / "deploy_vector_backend_devcloud.sh"
REGULAR_DEPLOY = REPO_ROOT / "deploy_devcloud.sh"
RUNTIME = REPO_ROOT / "scripts" / "devcloud_runtime.sh"
RUNBOOK = REPO_ROOT / "docs" / "devcloud-container-deployment.md"


def test_vector_bootstrap_keeps_model_and_index_outside_the_code_archive():
    body = VECTOR_DEPLOY.read_text(encoding="utf-8")

    assert "feedback_hub/data/models/Qwen3-Embedding-0.6B" in body
    assert "feedback_hub/data/vector_index" in body
    assert "requirements-vector.txt" in body
    assert "--start-after-bootstrap" in body
    assert "ingest backfill" not in body
    assert "feedback_hub/data/models" in REGULAR_DEPLOY.read_text(encoding="utf-8")
    assert "feedback_hub/data/vector_index" in REGULAR_DEPLOY.read_text(encoding="utf-8")


def test_vector_bootstrap_validates_files_checksums_cpu_torch_and_safe_uploads():
    body = VECTOR_DEPLOY.read_text(encoding="utf-8")

    for required_file in ("config.json", "tokenizer.json", "model.safetensors"):
        assert required_file in body
    assert "sha256" in body.lower()
    assert "upload" in body.lower()
    assert "mktemp" in body
    assert "mv" in body
    assert "download.pytorch.org/whl/cpu" in body
    assert "torch.version.cuda is None" in body
    assert "manifest_is_safe" in body
    assert "--" in body


def test_regular_deploy_swaps_before_vector_then_app_restart_with_rollback():
    body = REGULAR_DEPLOY.read_text(encoding="utf-8")

    assert body.index('mv "${NEW}" "${OLD}"') < body.index("scripts/vector_runtime.sh restart")
    assert body.index("scripts/vector_runtime.sh restart") < body.index("scripts/devcloud_runtime.sh restart")
    assert "rollback" in body.lower()
    assert "feedback_hub/data/vector_index" in body
    assert "feedback_hub/data/models/Qwen3-Embedding-0.6B" in body
    # A failed vector health restart must not fall through to serving the app.
    assert 'cd "$OLD" && APP_PORT="$APP_PORT" scripts/devcloud_runtime.sh start || true' not in body


def test_runtime_exposes_vector_health_coordination_for_deploys():
    body = RUNTIME.read_text(encoding="utf-8")

    assert "vector_index_is_active" in body
    assert "VECTOR_PORT" in body
    assert "/health" in body
    assert "vector_runtime.sh" in body


def test_vector_deploy_rejects_missing_required_model_file_before_ssh(tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for executable in ("ssh", "scp"):
        path = fake_bin / executable
        path.write_text("#!/bin/sh\necho unexpected >&2\nexit 99\n", encoding="utf-8")
        path.chmod(0o755)

    result = subprocess.run(
        ["bash", str(VECTOR_DEPLOY)],
        env={
            **os.environ,
            "MODEL_SOURCE_DIR": str(model_dir),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "model.safetensors" in result.stderr
    assert "unexpected" not in result.stderr


def test_vector_deploy_accepts_a_valid_model_manifest_before_first_ssh(tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    for filename in ("config.json", "tokenizer.json", "model.safetensors"):
        (model_dir / filename).write_text("fixture", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ssh = fake_bin / "ssh"
    ssh.write_text("#!/bin/sh\necho ssh-reached >&2\nexit 37\n", encoding="utf-8")
    ssh.chmod(0o755)

    result = subprocess.run(
        ["bash", str(VECTOR_DEPLOY)],
        env={
            **os.environ,
            "MODEL_SOURCE_DIR": str(model_dir),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 37
    assert "ssh-reached" in result.stderr


def test_vector_deploy_runbook_documents_bootstrap_start_health_and_bounded_rollback():
    body = RUNBOOK.read_text(encoding="utf-8")

    for value in (
        "/opt/feedback_hub/feedback_hub/data/models/Qwen3-Embedding-0.6B",
        "127.0.0.1:8011",
        "TOPIC_MINING_API_TOKEN",
        "--last 14d",
        "crontab-pre-vector.txt",
        "active-generation.json",
    ):
        assert value in body


def test_vector_deploy_scripts_parse_without_contacting_remote_hosts():
    for script in (VECTOR_DEPLOY, REGULAR_DEPLOY, RUNTIME):
        result = subprocess.run(["bash", "-n", str(script)], text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
