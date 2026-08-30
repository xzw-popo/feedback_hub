"""Contract tests for the thin cron wrapper around the Python pipeline."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "feedback_incremental_sync.sh"


def test_incremental_script_is_thin_and_never_runs_tagging_or_owns_a_lock():
    body = SCRIPT.read_text(encoding="utf-8")
    assert body.startswith("#!/usr/bin/env bash\n")
    assert "set -euo pipefail" in body
    assert 'pipeline incremental --pull-window 30m' in body
    assert "feedback_hub.cli tag" not in body
    assert "flock" not in body
    assert "mkdir \"$LOCK" not in body
    assert "feedback_hub/data/logs" in body
    assert "TOKEN" not in body and "SECRET" not in body
    assert "rotate_log || true" in body
    assert "2>/dev/null || true" in body
    assert "{ wc -c < \"$LOG_FILE\"; } 2>/dev/null || printf '0'" in body
    assert 'size="${size//[[:space:]]/}"' in body
    assert "LOG_KEEP must be an integer between 1 and 20" in body
    assert "LOG_MAX_BYTES must be an integer between 1 and 1073741824" in body


def test_incremental_script_handles_space_paths_and_propagates_python_exit(tmp_path):
    project = tmp_path / "project with spaces"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    wrapper = scripts / SCRIPT.name
    wrapper.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    wrapper.chmod(0o755)
    fake_python = project / "fake python"
    fake_python.write_text(
        "#!/usr/bin/env bash\nprintf '<%s>\\n' \"$*\"\nexit \"${FAKE_EXIT:-0}\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    log_dir = project / "logs with spaces"
    completed = subprocess.run(
        [str(wrapper)], text=True, capture_output=True,
        env={**os.environ, "PYTHON_BIN": str(fake_python), "LOG_DIR": str(log_dir), "FAKE_EXIT": "17"},
    )
    assert completed.returncode == 17
    assert completed.stdout == ""
    assert completed.stderr == ""
    log = (log_dir / "feedback_incremental_sync.log").read_text(encoding="utf-8")
    assert "<-m feedback_hub.cli pipeline incremental --pull-window 30m>" in log


def test_incremental_script_rotates_only_bounded_log_files_before_running(tmp_path):
    project = tmp_path / "project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    wrapper = scripts / SCRIPT.name
    wrapper.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    wrapper.chmod(0o755)
    fake_python = project / "python"
    fake_python.write_text("#!/usr/bin/env bash\nprintf 'new-run\\n'\n", encoding="utf-8")
    fake_python.chmod(0o755)
    log_dir = project / "logs"
    log_dir.mkdir()
    log_file = log_dir / "feedback_incremental_sync.log"
    log_file.write_text("previous-run\n", encoding="utf-8")
    (log_dir / "feedback_incremental_sync.log.1").write_text("older-run\n", encoding="utf-8")
    completed = subprocess.run(
        [str(wrapper)], text=True, capture_output=True,
        env={**os.environ, "PYTHON_BIN": str(fake_python), "LOG_DIR": str(log_dir),
             "LOG_MAX_BYTES": "1", "LOG_KEEP": "2"},
    )
    assert completed.returncode == 0
    assert (log_dir / "feedback_incremental_sync.log.1").read_text(encoding="utf-8") == "previous-run\n"
    assert (log_dir / "feedback_incremental_sync.log.2").read_text(encoding="utf-8") == "older-run\n"
    assert "new-run" in log_file.read_text(encoding="utf-8")
    assert not (log_dir / "feedback_incremental_sync.log.3").exists()


@pytest.mark.parametrize(("setting", "value"), [
    ("LOG_KEEP", "21"),
    ("LOG_KEEP", "nope"),
    ("LOG_MAX_BYTES", "1073741825"),
    ("LOG_MAX_BYTES", "1e6"),
])
def test_incremental_script_rejects_out_of_bounds_rotation_settings_before_python(tmp_path, setting, value):
    project = tmp_path / "project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    wrapper = scripts / SCRIPT.name
    wrapper.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    wrapper.chmod(0o755)
    fake_python = project / "python"
    fake_python.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
    fake_python.chmod(0o755)
    completed = subprocess.run(
        [str(wrapper)], text=True, capture_output=True,
        env={**os.environ, "PYTHON_BIN": str(fake_python), setting: value},
    )
    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr


def test_incremental_script_accepts_leading_zero_rotation_settings_decimal_safely(tmp_path):
    project = tmp_path / "project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    wrapper = scripts / SCRIPT.name
    wrapper.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    wrapper.chmod(0o755)
    fake_python = project / "python"
    fake_python.write_text("#!/usr/bin/env bash\nexit 75\n", encoding="utf-8")
    fake_python.chmod(0o755)
    completed = subprocess.run(
        [str(wrapper)], text=True, capture_output=True,
        env={**os.environ, "PYTHON_BIN": str(fake_python), "LOG_KEEP": "0002", "LOG_MAX_BYTES": "000001"},
    )
    assert completed.returncode == 75


def test_incremental_script_concurrent_rotation_never_prevents_python_start(tmp_path):
    project = tmp_path / "project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    wrapper = scripts / SCRIPT.name
    wrapper.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    wrapper.chmod(0o755)
    fake_python = project / "python"
    fake_python.write_text(
        "#!/usr/bin/env bash\nmkdir -p \"$START_DIR\"\ntouch \"$START_DIR/$PPID\"\nprintf '{\\\"status\\\":\\\"skipped_locked\\\"}\\n'\nexit 75\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    log_dir = project / "logs"
    log_dir.mkdir()
    started = project / "started"
    environment = {
        **os.environ, "PYTHON_BIN": str(fake_python), "LOG_DIR": str(log_dir),
        "LOG_MAX_BYTES": "1", "LOG_KEEP": "0002", "START_DIR": str(started),
    }
    worker_count = 2
    trials = 50
    for _trial in range(trials):
        (log_dir / "feedback_incremental_sync.log").write_text("rotate me\n", encoding="utf-8")
        runs = [subprocess.Popen(
            [str(wrapper)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment,
        ) for _ in range(worker_count)]
        completed = [run.communicate(timeout=10) for run in runs]
        assert [run.returncode for run in runs] == [75] * worker_count
        assert all(stderr == "" for _stdout, stderr in completed)
    assert len(list(started.iterdir())) == worker_count * trials
