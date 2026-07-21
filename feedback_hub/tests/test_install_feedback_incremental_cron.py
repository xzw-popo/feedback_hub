"""Black-box contract tests for the idempotent incremental-sync cron installer."""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "install_feedback_incremental_cron.sh"


def _fake_crontab(path: Path) -> Path:
    path.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
state="${FAKE_CRONTAB_STATE:?}"
case "${1:-}" in
  -l)
    if [[ -f "$state" ]]; then cat "$state"; else exit 1; fi
    ;;
  -r)
    rm -f -- "$state"
    ;;
  *)
    if [[ "${FAKE_INSTALL_FAIL:-0}" == 1 ]]; then exit 42; fi
    cp -- "$1" "$state"
    if [[ "${FAKE_CORRUPT_ONCE:-0}" == 1 && ! -e "${FAKE_CORRUPT_MARKER:?}" ]]; then
      : > "$FAKE_CORRUPT_MARKER"
      printf 'corrupt\\n' > "$state"
    fi
    ;;
esac
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def run_installer(tmp_path: Path, existing_crontab: str | None, *args: str, extra_env: dict[str, str] | None = None):
    project = tmp_path / "project with spaces"
    (project / "feedback_hub" / "data").mkdir(parents=True)
    state = tmp_path / "crontab-state"
    if existing_crontab is not None:
        state.write_text(existing_crontab, encoding="utf-8")
    fake = _fake_crontab(tmp_path / "fake-crontab")
    completed = subprocess.run(
        [str(SCRIPT), "--project-dir", str(project), *args],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "CRONTAB_BIN": str(fake),
            "FAKE_CRONTAB_STATE": str(state),
            **(extra_env or {}),
        },
    )
    return completed, project, state


def test_installer_replaces_only_legacy_entry_and_preserves_unrelated_lines(tmp_path):
    existing = (
        "# keep this comment mentioning feedback_incremental_sync.sh\n"
        "MAILTO=ops@example.test\n"
        "\n"
        "0 */2 * * * cd /opt/feedback_hub && scripts/feedback_daily_sync.sh --last 150m --no-sync\n"
        "0 1 * * * cd /opt/feedback_hub && scripts/feedback_daily_sync.sh --last 30m\n"
        "*/20 * * * * cd /old && PYTHON_BIN=./.venv/bin/python scripts/feedback_incremental_sync.sh\n"
        "15 4 * * * /opt/other.sh\n"
    )
    completed, project, state = run_installer(tmp_path, existing)

    assert completed.returncode == 0, completed.stderr
    installed = state.read_text(encoding="utf-8")
    expected = (
        f"*/20 * * * * cd '{project.resolve()}' && PYTHON_BIN=./.venv/bin/python scripts/feedback_incremental_sync.sh"
    )
    assert installed.count("feedback_incremental_sync.sh") == 2  # command plus preserved comment
    assert installed.count(expected) == 1
    assert "feedback_daily_sync.sh --last 150m --no-sync" not in installed
    assert "feedback_daily_sync.sh --last 30m" in installed
    assert "MAILTO=ops@example.test" in installed
    assert "\n\n0 1" in installed
    assert installed.endswith("\n")
    assert "/opt/other.sh" in installed


def test_installer_creates_private_collision_safe_backup_when_no_crontab_exists(tmp_path):
    completed, project, state = run_installer(tmp_path, None)

    assert completed.returncode == 0, completed.stderr
    assert state.exists()
    backups = list((project / "feedback_hub" / "data" / "cron-backups").glob("crontab-*.txt"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == ""
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600


def test_installer_dry_run_prints_candidate_without_mutating_crontab_or_backups(tmp_path):
    existing = "15 4 * * * /opt/other.sh\n"
    completed, project, state = run_installer(tmp_path, existing, "--dry-run")

    assert completed.returncode == 0, completed.stderr
    assert state.read_text(encoding="utf-8") == existing
    assert "Candidate crontab (dry-run; no backup or crontab mutation):" in completed.stdout
    assert "*/20 * * * * cd '" in completed.stdout
    backup_dir = project / "feedback_hub" / "data" / "cron-backups"
    assert not backup_dir.exists() or not list(backup_dir.iterdir())


def test_installer_rolls_back_when_readback_verification_fails(tmp_path):
    existing = "15 4 * * * /opt/other.sh\n"
    marker = tmp_path / "corrupted-once"
    completed, _project, state = run_installer(
        tmp_path,
        existing,
        extra_env={"FAKE_CORRUPT_ONCE": "1", "FAKE_CORRUPT_MARKER": str(marker)},
    )

    assert completed.returncode != 0
    assert "verification failed" in completed.stderr
    assert state.read_text(encoding="utf-8") == existing


@pytest.mark.parametrize(("name", "reason"), [("bad'project", "single quote"), ("bad\nproject", "control characters")])
def test_installer_rejects_single_quote_and_control_characters_in_project_path(tmp_path, name, reason):
    project = tmp_path / name
    (project / "feedback_hub" / "data").mkdir(parents=True)
    state = tmp_path / "crontab-state"
    fake = _fake_crontab(tmp_path / "fake-crontab")

    completed = subprocess.run(
        [str(SCRIPT), "--project-dir", str(project)],
        text=True,
        capture_output=True,
        env={**os.environ, "CRONTAB_BIN": str(fake), "FAKE_CRONTAB_STATE": str(state)},
    )

    assert completed.returncode == 2
    assert reason in completed.stderr
    assert not state.exists()


def test_installer_is_idempotent_under_two_concurrent_invocations(tmp_path):
    project = tmp_path / "project"
    (project / "feedback_hub" / "data").mkdir(parents=True)
    state = tmp_path / "crontab-state"
    state.write_text("15 4 * * * /opt/other.sh\n", encoding="utf-8")
    fake = _fake_crontab(tmp_path / "fake-crontab")
    env = {**os.environ, "CRONTAB_BIN": str(fake), "FAKE_CRONTAB_STATE": str(state)}
    runs = [
        subprocess.Popen([str(SCRIPT), "--project-dir", str(project)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        for _ in range(2)
    ]
    completed = [run.communicate(timeout=10) for run in runs]

    assert [run.returncode for run in runs] == [0, 0], completed
    installed = state.read_text(encoding="utf-8")
    assert installed.count("scripts/feedback_incremental_sync.sh") == 1
    assert installed.count("/opt/other.sh") == 1
