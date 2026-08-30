"""Black-box contract tests for the idempotent incremental-sync cron installer."""
from __future__ import annotations

import os
import signal
import stat
import subprocess
import time
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
    if [[ -n "${FAKE_LIST_EXIT:-}" ]]; then exit "$FAKE_LIST_EXIT"; fi
    if [[ -f "$state" ]]; then cat "$state"; else exit 1; fi
    ;;
  -r)
    rm -f -- "$state"
    ;;
  *)
    if [[ "${FAKE_INSTALL_FAIL:-0}" == 1 ]]; then exit 42; fi
    if [[ -n "${FAKE_ACTIVE_LOCK:-}" ]]; then
      if ! mkdir "$FAKE_ACTIVE_LOCK" 2>/dev/null; then : > "${FAKE_OVERLAP_MARKER:?}"; fi
    fi
    if [[ -n "${FAKE_INSTALL_READY:-}" ]]; then : > "$FAKE_INSTALL_READY"; fi
    if [[ -n "${FAKE_INSTALL_DELAY:-}" ]]; then
      if [[ -n "${FAKE_INSTALL_DELAY_AFTER_MARKER:-}" && ! -e "$FAKE_INSTALL_DELAY_AFTER_MARKER" ]]; then
        :
      elif [[ -z "${FAKE_INSTALL_DELAY_ONCE_MARKER:-}" || ! -e "$FAKE_INSTALL_DELAY_ONCE_MARKER" ]]; then
        [[ -z "${FAKE_INSTALL_DELAY_ONCE_MARKER:-}" ]] || : > "$FAKE_INSTALL_DELAY_ONCE_MARKER"
        sleep "$FAKE_INSTALL_DELAY"
      fi
    fi
    cp -- "$1" "$state"
    [[ -z "${FAKE_ACTIVE_LOCK:-}" ]] || rmdir "$FAKE_ACTIVE_LOCK" 2>/dev/null || true
    if [[ -n "${FAKE_POST_COPY_READY:-}" ]]; then : > "$FAKE_POST_COPY_READY"; fi
    if [[ -n "${FAKE_POST_COPY_DELAY:-}" ]]; then
      if [[ -z "${FAKE_POST_COPY_DELAY_ONCE_MARKER:-}" || ! -e "$FAKE_POST_COPY_DELAY_ONCE_MARKER" ]]; then
        [[ -z "${FAKE_POST_COPY_DELAY_ONCE_MARKER:-}" ]] || : > "$FAKE_POST_COPY_DELAY_ONCE_MARKER"
        sleep "$FAKE_POST_COPY_DELAY"
      fi
    fi
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
        "# feedback-hub: managed incremental sync\n"
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
    assert installed.count("# feedback-hub: managed incremental sync") == 1
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


@pytest.mark.parametrize(
    ("name", "reason"),
    [("bad'project", "single quote"), ("bad\nproject", "control characters"), ("bad%project", "percent")],
)
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


def test_installer_removes_only_exact_managed_and_legacy_jobs(tmp_path):
    existing = (
        "# feedback-hub: managed incremental sync\n"
        "*/20 * * * * cd /old && PYTHON_BIN=./.venv/bin/python scripts/feedback_incremental_sync.sh\n"
        "0 */2 * * * cd /opt/feedback_hub && scripts/feedback_daily_sync.sh --last 150m --no-sync\n"
        "0 */2 * * * cd /opt/feedback_hub && scripts/feedback_daily_sync.sh --last 150m --no-sync.disabled\n"
        "0 */2 * * * echo scripts/feedback_daily_sync.sh --last 150m --no-sync\n"
        "0 */2 * * * cd /opt/feedback_hub && scripts/feedback_daily_sync.sh --last 150m --no-sync --audit\n"
        "MAILTO=feedback_incremental_sync.sh\n"
        "# scripts/feedback_incremental_sync.sh is mentioned here\n"
    )
    completed, _project, state = run_installer(tmp_path, existing)

    assert completed.returncode == 0, completed.stderr
    installed = state.read_text(encoding="utf-8")
    assert "cd /old && PYTHON_BIN=./.venv/bin/python scripts/feedback_incremental_sync.sh" not in installed
    assert "cd /opt/feedback_hub && scripts/feedback_daily_sync.sh --last 150m --no-sync\n" not in installed
    assert "--no-sync.disabled" in installed
    assert "echo scripts/feedback_daily_sync.sh --last 150m --no-sync" in installed
    assert "--no-sync --audit" in installed
    assert "MAILTO=feedback_incremental_sync.sh" in installed
    assert "# scripts/feedback_incremental_sync.sh is mentioned here" in installed


def test_installer_documents_byte_exact_production_entry():
    body = SCRIPT.read_text(encoding="utf-8")
    assert (
        "*/20 * * * * cd /opt/feedback_hub && "
        "PYTHON_BIN=./.venv/bin/python scripts/feedback_incremental_sync.sh"
    ) in body


def test_installer_uses_one_global_lock_for_two_projects(tmp_path):
    projects = [tmp_path / "one", tmp_path / "two"]
    for project in projects:
        (project / "feedback_hub" / "data").mkdir(parents=True)
    state = tmp_path / "crontab-state"
    state.write_text("15 4 * * * /opt/other.sh\n", encoding="utf-8")
    fake = _fake_crontab(tmp_path / "fake-crontab")
    active_lock = tmp_path / "fake-active"
    overlap = tmp_path / "fake-overlap"
    env = {
        **os.environ,
        "TMPDIR": str(tmp_path),
        "CRONTAB_BIN": str(fake),
        "FAKE_CRONTAB_STATE": str(state),
        "FAKE_ACTIVE_LOCK": str(active_lock),
        "FAKE_OVERLAP_MARKER": str(overlap),
        "FAKE_INSTALL_DELAY": "0.2",
    }
    runs = [
        subprocess.Popen([str(SCRIPT), "--project-dir", str(project)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        for project in projects
    ]
    output = [run.communicate(timeout=10) for run in runs]

    assert [run.returncode for run in runs] == [0, 0], output
    assert not overlap.exists()
    installed = state.read_text(encoding="utf-8")
    assert installed.count("scripts/feedback_incremental_sync.sh") == 1
    assert installed.count("/opt/other.sh") == 1


def test_foreign_global_lock_is_never_removed_or_bypassed(tmp_path):
    project = tmp_path / "project"
    (project / "feedback_hub" / "data").mkdir(parents=True)
    state = tmp_path / "crontab-state"
    state.write_text("15 4 * * * /opt/other.sh\n", encoding="utf-8")
    fake = _fake_crontab(tmp_path / "fake-crontab")
    lock = Path(f"/tmp/feedback-incremental-crontab-{os.getuid()}.lock")
    lock.unlink(missing_ok=True)
    lock.write_text(f"{os.getpid()} foreign-owner\n", encoding="utf-8")
    try:
        completed = subprocess.run(
            [str(SCRIPT), "--project-dir", str(project)],
            text=True,
            capture_output=True,
            env={
                **os.environ,
                "TMPDIR": str(tmp_path),
                "CRONTAB_BIN": str(fake),
                "FAKE_CRONTAB_STATE": str(state),
                "LOCK_RETRY_ATTEMPTS": "1",
                "LOCK_RETRY_DELAY": "0",
            },
        )

        assert completed.returncode != 0
        assert state.read_text(encoding="utf-8") == "15 4 * * * /opt/other.sh\n"
        assert lock.read_text(encoding="utf-8") == f"{os.getpid()} foreign-owner\n"
    finally:
        lock.unlink(missing_ok=True)


def test_definitely_dead_global_lock_is_left_for_manual_removal(tmp_path):
    project = tmp_path / "project"
    (project / "feedback_hub" / "data").mkdir(parents=True)
    state = tmp_path / "crontab-state"
    state.write_text("15 4 * * * /opt/other.sh\n", encoding="utf-8")
    fake = _fake_crontab(tmp_path / "fake-crontab")
    lock = Path(f"/tmp/feedback-incremental-crontab-{os.getuid()}.lock")
    lock.unlink(missing_ok=True)
    lock.write_text("99999999 dead-owner\n", encoding="utf-8")
    try:
        completed = subprocess.run(
            [str(SCRIPT), "--project-dir", str(project)],
            text=True,
            capture_output=True,
            env={
                **os.environ,
                "CRONTAB_BIN": str(fake),
                "FAKE_CRONTAB_STATE": str(state),
                "LOCK_RETRY_DELAY": "0",
            },
        )
        assert completed.returncode == 75
        assert lock.read_text(encoding="utf-8") == "99999999 dead-owner\n"
    finally:
        lock.unlink(missing_ok=True)


def test_term_releases_only_its_owned_global_lock(tmp_path):
    project = tmp_path / "project"
    (project / "feedback_hub" / "data").mkdir(parents=True)
    state = tmp_path / "crontab-state"
    state.write_text("15 4 * * * /opt/other.sh\n", encoding="utf-8")
    fake = _fake_crontab(tmp_path / "fake-crontab")
    ready = tmp_path / "install-ready"
    env = {
        **os.environ,
        "TMPDIR": str(tmp_path),
        "CRONTAB_BIN": str(fake),
        "FAKE_CRONTAB_STATE": str(state),
        "FAKE_INSTALL_READY": str(ready),
        "FAKE_INSTALL_DELAY": "10",
        "FAKE_INSTALL_DELAY_ONCE_MARKER": str(tmp_path / "install-delayed"),
    }
    run = subprocess.Popen(
        [str(SCRIPT), "--project-dir", str(project)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
    )
    deadline = time.monotonic() + 5
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ready.exists()
    os.killpg(run.pid, signal.SIGTERM)
    stdout, stderr = run.communicate(timeout=5)

    assert run.returncode != 0, (stdout, stderr)
    assert not (tmp_path / f"feedback-incremental-crontab-{os.getuid()}.lock").exists()


def test_read_failure_other_than_no_crontab_aborts_before_backup_or_mutation(tmp_path):
    existing = "15 4 * * * /opt/other.sh\n"
    completed, project, state = run_installer(tmp_path, existing, extra_env={"FAKE_LIST_EXIT": "42"})

    assert completed.returncode == 42
    assert state.read_text(encoding="utf-8") == existing
    backup_dir = project / "feedback_hub" / "data" / "cron-backups"
    assert not backup_dir.exists()
    assert "Unable to read current crontab" in completed.stderr


def test_installer_replaces_exact_legacy_production_job_with_env_and_redirect(tmp_path):
    existing = (
        "0 */2 * * * cd /opt/feedback_hub && "
        "PYTHON_BIN=./.venv/bin/python APP_PORT=8000 "
        "scripts/feedback_daily_sync.sh --last 150m --no-sync "
        ">> /opt/feedback_hub/feedback_hub/data/logs/daily.log 2>&1\n"
        "0 */2 * * * cd /opt/feedback_hub && PYTHON_BIN=./.venv/bin/python "
        "scripts/feedback_daily_sync.sh --last 150m --no-sync --audit\n"
    )
    completed, _project, state = run_installer(tmp_path, existing)

    assert completed.returncode == 0, completed.stderr
    installed = state.read_text(encoding="utf-8")
    assert "APP_PORT=8000" not in installed
    assert "--no-sync --audit" in installed


def test_global_lock_is_shared_even_when_callers_set_different_tmpdirs(tmp_path):
    projects = [tmp_path / "one", tmp_path / "two"]
    tmpdirs = [tmp_path / "tmp-one", tmp_path / "tmp-two"]
    for project, temp_dir in zip(projects, tmpdirs):
        (project / "feedback_hub" / "data").mkdir(parents=True)
        temp_dir.mkdir()
    state = tmp_path / "crontab-state"
    state.write_text("15 4 * * * /opt/other.sh\n", encoding="utf-8")
    fake = _fake_crontab(tmp_path / "fake-crontab")
    active_lock = tmp_path / "fake-active"
    overlap = tmp_path / "fake-overlap"
    runs = []
    for project, temp_dir in zip(projects, tmpdirs):
        runs.append(subprocess.Popen(
            [str(SCRIPT), "--project-dir", str(project)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                **os.environ,
                "TMPDIR": str(temp_dir),
                "CRONTAB_BIN": str(fake),
                "FAKE_CRONTAB_STATE": str(state),
                "FAKE_ACTIVE_LOCK": str(active_lock),
                "FAKE_OVERLAP_MARKER": str(overlap),
                "FAKE_INSTALL_DELAY": "0.2",
            },
        ))
    output = [run.communicate(timeout=10) for run in runs]

    assert [run.returncode for run in runs] == [0, 0], output
    assert not overlap.exists()
    assert state.read_text(encoding="utf-8").count("scripts/feedback_incremental_sync.sh") == 1


def test_term_after_candidate_copy_restores_previous_crontab_and_releases_global_lock(tmp_path):
    project = tmp_path / "project"
    (project / "feedback_hub" / "data").mkdir(parents=True)
    existing = "15 4 * * * /opt/other.sh\n"
    state = tmp_path / "crontab-state"
    state.write_text(existing, encoding="utf-8")
    fake = _fake_crontab(tmp_path / "fake-crontab")
    ready = tmp_path / "candidate-copied"
    run = subprocess.Popen(
        [str(SCRIPT), "--project-dir", str(project)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            **os.environ,
            "CRONTAB_BIN": str(fake),
            "FAKE_CRONTAB_STATE": str(state),
            "FAKE_POST_COPY_READY": str(ready),
            "FAKE_POST_COPY_DELAY": "10",
            "FAKE_INSTALL_DELAY_ONCE_MARKER": str(tmp_path / "copy-delayed"),
            "FAKE_POST_COPY_DELAY_ONCE_MARKER": str(tmp_path / "copy-post-delayed"),
        },
        start_new_session=True,
    )
    deadline = time.monotonic() + 5
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ready.exists()
    os.killpg(run.pid, signal.SIGTERM)
    stdout, stderr = run.communicate(timeout=5)

    assert run.returncode != 0, (stdout, stderr)
    assert state.read_text(encoding="utf-8") == existing
    assert not Path(f"/tmp/feedback-incremental-crontab-{os.getuid()}.lock").exists()


def test_immediate_signal_after_global_lock_appearance_never_leaves_a_lock(tmp_path):
    project = tmp_path / "project"
    (project / "feedback_hub" / "data").mkdir(parents=True)
    state = tmp_path / "crontab-state"
    state.write_text("15 4 * * * /opt/other.sh\n", encoding="utf-8")
    fake = _fake_crontab(tmp_path / "fake-crontab")
    lock = Path(f"/tmp/feedback-incremental-crontab-{os.getuid()}.lock")
    for _ in range(10):
        run = subprocess.Popen(
            [str(SCRIPT), "--project-dir", str(project)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                **os.environ,
                "CRONTAB_BIN": str(fake),
                "FAKE_CRONTAB_STATE": str(state),
                "FAKE_INSTALL_DELAY": "10",
                "FAKE_INSTALL_DELAY_ONCE_MARKER": str(tmp_path / f"delay-once-{_}"),
            },
            start_new_session=True,
        )
        deadline = time.monotonic() + 2
        while not lock.exists() and time.monotonic() < deadline:
            time.sleep(0.002)
        assert lock.exists()
        os.killpg(run.pid, signal.SIGTERM)
        run.communicate(timeout=5)
        assert not lock.exists()


def test_second_term_cannot_interrupt_delayed_signal_rollback(tmp_path):
    project = tmp_path / "project"
    (project / "feedback_hub" / "data").mkdir(parents=True)
    existing = "15 4 * * * /opt/other.sh\n"
    state = tmp_path / "crontab-state"
    state.write_text(existing, encoding="utf-8")
    fake = _fake_crontab(tmp_path / "fake-crontab")
    copied = tmp_path / "candidate-copied"
    post_marker = tmp_path / "post-copy-marker"
    run = subprocess.Popen(
        [str(SCRIPT), "--project-dir", str(project)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            **os.environ,
            "CRONTAB_BIN": str(fake),
            "FAKE_CRONTAB_STATE": str(state),
            "FAKE_POST_COPY_READY": str(copied),
            "FAKE_POST_COPY_DELAY": "10",
            "FAKE_POST_COPY_DELAY_ONCE_MARKER": str(post_marker),
            "FAKE_INSTALL_DELAY": "0.3",
            "FAKE_INSTALL_DELAY_AFTER_MARKER": str(post_marker),
        },
        start_new_session=True,
    )
    deadline = time.monotonic() + 5
    while not copied.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert copied.exists()
    os.killpg(run.pid, signal.SIGTERM)
    time.sleep(0.05)
    os.killpg(run.pid, signal.SIGTERM)
    stdout, stderr = run.communicate(timeout=5)

    assert run.returncode != 0, (stdout, stderr)
    assert state.read_text(encoding="utf-8") == existing
