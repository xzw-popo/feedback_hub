"""Static safety checks for the dedicated vector service lifecycle script."""
from __future__ import annotations

from pathlib import Path
import os
import subprocess


VECTOR_RUNTIME = Path(__file__).parents[2] / "scripts" / "vector_runtime.sh"


def test_vector_runtime_binds_only_loopback():
    body = VECTOR_RUNTIME.read_text(encoding="utf-8")
    assert "--host 127.0.0.1" in body
    assert "vector_index.pid" in body


def test_vector_runtime_installs_cpu_torch_and_refuses_empty_start_by_default():
    body = VECTOR_RUNTIME.read_text(encoding="utf-8")
    assert "download.pytorch.org/whl/cpu" in body
    assert "torch.version.cuda is None" in body
    assert "requirements.txt" in body
    assert "requirements-vector.txt" in body
    assert "VECTOR_ALLOW_EMPTY_INDEX" in body
    assert "manifest.json" in body


def test_vector_runtime_waits_for_a_healthy_loopback_service_before_success():
    body = VECTOR_RUNTIME.read_text(encoding="utf-8")
    assert "wait_for_health" in body
    assert "http://127.0.0.1:${VECTOR_PORT}/health" in body
    assert "curl -fsS" in body


def test_vector_runtime_checks_bootstrap_survival_and_existing_service_health():
    body = VECTOR_RUNTIME.read_text(encoding="utf-8")
    assert "wait_for_process" in body
    assert "wait_for_health \"$old_pid\"" in body


def test_vector_runtime_is_executable_and_git_tracks_executable_mode():
    assert os.stat(VECTOR_RUNTIME).st_mode & 0o111
    result = subprocess.run(
        ["git", "ls-files", "-s", "scripts/vector_runtime.sh"],
        cwd=VECTOR_RUNTIME.parents[1], capture_output=True, text=True, check=True,
    )
    assert result.stdout.startswith("100755 ")


def test_vector_runtime_uses_token_bound_pid_ownership():
    body = VECTOR_RUNTIME.read_text(encoding="utf-8")
    assert "--runtime-token" in body
    assert "pid_is_owned" in body
    assert "kill -9" in body
