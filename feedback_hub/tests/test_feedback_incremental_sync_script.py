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
