"""Static safety checks for the dedicated vector service lifecycle script."""
from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess
import sys
import time


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


def test_runtime_treats_zombie_as_stopped_without_leaving_a_pid_record(tmp_path):
    script = tmp_path / "scripts" / "vector_runtime.sh"
    script.parent.mkdir()
    shutil.copy2(VECTOR_RUNTIME, script)
    parent = subprocess.Popen(
        [sys.executable, "-c", "import os,time; child=os.fork(); child or os._exit(0); print(child, flush=True); time.sleep(20)"],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert parent.stdout is not None
        zombie_pid = parent.stdout.readline().strip()
        time.sleep(0.1)
        check = subprocess.run(
            ["bash", "-c", 'VECTOR_RUNTIME_LIBRARY=1 source "$1"; set +e; ps(){ printf "Z\\n"; }; process_alive "$2"', "bash", str(script), zombie_pid],
            capture_output=True, text=True,
        )
        assert check.returncode == 1
    finally:
        parent.terminate()
        parent.wait(timeout=5)


def test_runtime_escalates_owned_term_ignoring_process_without_leaking_pid_record(tmp_path):
    script = tmp_path / "scripts" / "vector_runtime.sh"
    script.parent.mkdir()
    shutil.copy2(VECTOR_RUNTIME, script)
    stubborn = subprocess.Popen(["bash", "-c", "trap '' TERM; while :; do /bin/sleep 1; done"])
    try:
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "vector_index.pid").write_text(f"{stubborn.pid} token123\n", encoding="utf-8")
        counter = tmp_path / "ps-count"
        counter.write_text("0", encoding="utf-8")
        result = subprocess.run(
            ["bash", "-c", (
                'VECTOR_RUNTIME_LIBRARY=1 source "$1"; set +e; pid_is_owned(){ return 0; }; '
                'counter_path="$2"; sleep(){ :; }; ps(){ n=$(cat "$counter_path"); n=$((n+1)); printf "%s" "$n" > "$counter_path"; '
                'if [ "$n" -gt 11 ]; then printf "Z\\n"; else printf "S\\n"; fi; }; stop_app'
            ), "bash", str(script), str(counter)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert not (run_dir / "vector_index.pid").exists()
        exit_code = None
        for _ in range(20):
            exit_code = stubborn.poll()
            if exit_code is not None:
                break
            time.sleep(0.1)
        assert exit_code == -9
    finally:
        if stubborn.poll() is None:
            stubborn.kill()
            stubborn.wait(timeout=5)
