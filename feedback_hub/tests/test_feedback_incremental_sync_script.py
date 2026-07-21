"""Contract tests for the thin cron wrapper around the Python pipeline."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


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
