from __future__ import annotations

import os
import subprocess
import tarfile
import hashlib
import shutil
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
VECTOR_DEPLOY = REPO_ROOT / "scripts" / "deploy_vector_backend_devcloud.sh"
REGULAR_DEPLOY = REPO_ROOT / "deploy_devcloud.sh"
RUNTIME = REPO_ROOT / "scripts" / "devcloud_runtime.sh"
RUNBOOK = REPO_ROOT / "docs" / "devcloud-container-deployment.md"


def _remote_deploy_script() -> str:
    body = REGULAR_DEPLOY.read_text(encoding="utf-8")
    return body.split("<<'REMOTE_DEPLOY'\n", 1)[1].split("\nREMOTE_DEPLOY", 1)[0]


def _remote_model_finish_script() -> str:
    body = VECTOR_DEPLOY.read_text(encoding="utf-8")
    return body.split("<<'REMOTE_FINISH'\n", 1)[1].split("\nREMOTE_FINISH", 1)[0]


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _archive_for_remote_deploy(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    (source / "fresh.txt").write_text("new-code", encoding="utf-8")
    archive = tmp_path / "new-code.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname=".")
    return archive


def _archive_that_fails_after_code_swap(tmp_path: Path) -> Path:
    source = tmp_path / "swap-source"
    (source / "scripts").mkdir(parents=True)
    _write_executable(source / "scripts" / "devcloud_runtime.sh", "#!/bin/sh\nexit 70\n")
    _write_executable(source / "scripts" / "vector_runtime.sh", "#!/bin/sh\nexit 0\n")
    archive = tmp_path / "swap-failure.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname=".")
    return archive


def _archive_that_succeeds_and_arms_signal(tmp_path: Path) -> Path:
    source = tmp_path / "success-source"
    (source / "scripts").mkdir(parents=True)
    _write_executable(
        source / "scripts" / "devcloud_runtime.sh",
        "#!/bin/sh\ntouch \"${SIGNAL_READY:?}\"\nexit 0\n",
    )
    _write_executable(source / "scripts" / "vector_runtime.sh", "#!/bin/sh\nexit 0\n")
    (source / "new-marker").write_text("new", encoding="utf-8")
    archive = tmp_path / "success.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname=".")
    return archive


def _remote_old_tree(tmp_path: Path) -> Path:
    old = tmp_path / "feedback_hub"
    _write_executable(old / "scripts" / "devcloud_runtime.sh", "#!/bin/sh\nexit 0\n")
    (old / ".venv").mkdir(parents=True)
    (old / ".venv" / "state").write_text("venv", encoding="utf-8")
    (old / ".env").write_text("env", encoding="utf-8")
    (old / "feedback_hub" / "data").mkdir(parents=True)
    (old / "feedback_hub" / "data" / "state").write_text("data", encoding="utf-8")
    return old


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


def test_vector_bootstrap_rejects_any_model_symlink_before_ssh(tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    for filename in ("config.json", "tokenizer.json", "model.safetensors"):
        (model_dir / filename).write_text("fixture", encoding="utf-8")
    (model_dir / "linked-config.json").symlink_to(model_dir / "config.json")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "ssh", "#!/bin/sh\necho unexpected >&2\nexit 99\n")

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
    assert "symlink" in result.stderr.lower()
    assert "unexpected" not in result.stderr


def test_vector_bootstrap_rejects_a_symlinked_model_root_before_ssh(tmp_path):
    real_model = tmp_path / "real-model"
    real_model.mkdir()
    for filename in ("config.json", "tokenizer.json", "model.safetensors"):
        (real_model / filename).write_text("fixture", encoding="utf-8")
    source_link = tmp_path / "model-link"
    source_link.symlink_to(real_model, target_is_directory=True)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "ssh", "#!/bin/sh\necho unexpected >&2\nexit 99\n")

    result = subprocess.run(
        ["bash", str(VECTOR_DEPLOY)],
        env={
            **os.environ,
            "MODEL_SOURCE_DIR": str(source_link),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "symlink" in result.stderr.lower()
    assert "unexpected" not in result.stderr


def test_model_manifest_is_staged_and_promotion_has_a_restoring_trap():
    body = VECTOR_DEPLOY.read_text(encoding="utf-8")

    assert 'cp -- "$MANIFEST" "$STAGE/.manifest.sha256"' in body
    assert body.index('cp -- "$MANIFEST" "$STAGE/.manifest.sha256"') < body.index('critical_model_move "$STAGE" "$MODEL_DIR"')
    assert "restore_previous_model" in body
    assert "PROMOTION_LIVE_MOVED" in body


def test_model_promotion_fault_restores_prior_live_model(tmp_path):
    remote = tmp_path / "model-finish.sh"
    remote.write_text(_remote_model_finish_script(), encoding="utf-8")
    app = tmp_path / "app"
    model = app / "feedback_hub" / "data" / "models" / "Qwen3-Embedding-0.6B"
    stage = Path(f"{model}.upload.fixture")
    manifest = tmp_path / "manifest.sha256"
    for root, payload in ((model, "old"), (stage, "new")):
        root.mkdir(parents=True, exist_ok=True)
        (root / "old-marker").write_text(payload, encoding="utf-8")
        for filename in ("config.json", "tokenizer.json", "model.safetensors"):
            (root / filename).write_text(payload + filename, encoding="utf-8")
    (model / ".manifest.sha256").write_text("old-manifest\n", encoding="utf-8")
    lines = []
    for filename in ("config.json", "tokenizer.json", "model.safetensors"):
        digest = hashlib.sha256((stage / filename).read_bytes()).hexdigest()
        lines.append(f"{digest}  {filename}\n")
    manifest.write_text("".join(lines), encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    counter = tmp_path / "mv-count"
    _write_executable(
        fake_bin / "mv",
        "#!/bin/sh\n"
        "count=$(cat \"${FAKE_MV_COUNT_FILE}\" 2>/dev/null || echo 0)\n"
        "count=$((count + 1)); printf '%s' \"$count\" > \"${FAKE_MV_COUNT_FILE}\"\n"
        "if [ \"$count\" = 2 ]; then exit 91; fi\n"
        "exec /bin/mv \"$@\"\n",
    )

    result = subprocess.run(
        ["bash", str(remote), str(app), str(model), str(app / "feedback_hub" / "data" / "vector_index"), str(manifest), str(stage), "0"],
        env={**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}", "FAKE_MV_COUNT_FILE": str(counter)},
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert (model / "old-marker").read_text(encoding="utf-8") == "old"
    assert not Path(f"{model}.previous").exists()


def test_model_promotion_ignores_term_after_commit_before_previous_delete(tmp_path):
    remote = tmp_path / "model-finish.sh"
    remote.write_text(_remote_model_finish_script(), encoding="utf-8")
    app = tmp_path / "app"
    model = app / "feedback_hub" / "data" / "models" / "Qwen3-Embedding-0.6B"
    stage = Path(f"{model}.upload.fixture")
    manifest = tmp_path / "manifest.sha256"
    for root, payload in ((model, "old"), (stage, "new")):
        root.mkdir(parents=True, exist_ok=True)
        (root / "marker").write_text(payload, encoding="utf-8")
        for filename in ("config.json", "tokenizer.json", "model.safetensors"):
            (root / filename).write_text(payload + filename, encoding="utf-8")
    (model / ".manifest.sha256").write_text("old-manifest\n", encoding="utf-8")
    manifest.write_text(
        "".join(
            f"{hashlib.sha256((stage / filename).read_bytes()).hexdigest()}  {filename}\n"
            for filename in ("config.json", "tokenizer.json", "model.safetensors")
        ),
        encoding="utf-8",
    )
    _write_executable(app / ".venv" / "bin" / "python", "#!/bin/sh\ntouch \"${SIGNAL_READY:?}\"\nexit 0\n")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ready = tmp_path / "signal-ready"
    _write_executable(
        fake_bin / "rm",
        "#!/bin/sh\n"
        "for value in \"$@\"; do\n"
        "  if [ \"$value\" = \"${TARGET_PREVIOUS}\" ] && [ -e \"${SIGNAL_READY}\" ]; then kill -TERM \"$PPID\"; fi\n"
        "done\n"
        "exec /bin/rm \"$@\"\n",
    )

    result = subprocess.run(
        ["bash", str(remote), str(app), str(model), str(app / "feedback_hub" / "data" / "vector_index"), str(manifest), str(stage), "0"],
        env={
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "TARGET_PREVIOUS": f"{model}.previous",
            "SIGNAL_READY": str(ready),
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (model / "marker").read_text(encoding="utf-8") == "new"
    assert not Path(f"{model}.previous").exists()


def test_regular_deploy_swaps_before_vector_then_app_restart_with_rollback():
    body = REGULAR_DEPLOY.read_text(encoding="utf-8")

    assert body.index('critical_move "${NEW}" "${OLD}" NEW_PROMOTED') < body.index("scripts/vector_runtime.sh restart")
    assert body.index("scripts/vector_runtime.sh restart") < body.index("scripts/devcloud_runtime.sh restart")
    assert "rollback" in body.lower()
    assert "feedback_hub/data/vector_index" in body
    assert "feedback_hub/data/models/Qwen3-Embedding-0.6B" in body
    # A failed vector health restart must not fall through to serving the app.
    assert 'cd "$OLD" && APP_PORT="$APP_PORT" scripts/devcloud_runtime.sh start || true' not in body


@pytest.mark.parametrize("fail_mv_call", [2, 3, 4])
def test_remote_deploy_restores_every_moved_persistent_item_after_mv_fault(tmp_path, fail_mv_call):
    remote = tmp_path / "remote-deploy.sh"
    remote.write_text(_remote_deploy_script(), encoding="utf-8")
    old = _remote_old_tree(tmp_path)
    archive = _archive_for_remote_deploy(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    counter = tmp_path / "mv-count"
    _write_executable(
        fake_bin / "mv",
        "#!/bin/sh\n"
        "count_file=${FAKE_MV_COUNT_FILE:?}\n"
        "count=$(cat \"$count_file\" 2>/dev/null || echo 0)\n"
        "count=$((count + 1))\n"
        "printf '%s' \"$count\" > \"$count_file\"\n"
        "if [ \"$count\" = \"${FAIL_MV_CALL:?}\" ]; then exit 89; fi\n"
        "exec /bin/mv \"$@\"\n",
    )

    result = subprocess.run(
        ["bash", str(remote), str(old), str(archive), "8000"],
        env={
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "FAKE_MV_COUNT_FILE": str(counter),
            "FAIL_MV_CALL": str(fail_mv_call),
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert (old / ".venv" / "state").is_file(), result.stdout + result.stderr
    assert (old / ".venv" / "state").read_text(encoding="utf-8") == "venv"
    assert (old / ".env").read_text(encoding="utf-8") == "env"
    assert (old / "feedback_hub" / "data" / "state").read_text(encoding="utf-8") == "data"


def test_remote_deploy_treats_malformed_active_pointer_as_hard_error_before_swap(tmp_path):
    remote = tmp_path / "remote-deploy.sh"
    remote.write_text(_remote_deploy_script(), encoding="utf-8")
    old = _remote_old_tree(tmp_path)
    pointer = old / "feedback_hub" / "data" / "vector_index" / "active-generation.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text('{"generation_id":"../unsafe"}', encoding="utf-8")
    archive = _archive_for_remote_deploy(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "mv", "#!/bin/sh\necho unexpected-mv >&2\nexit 88\n")

    result = subprocess.run(
        ["bash", str(remote), str(old), str(archive), "8000"],
        env={**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "invalid active vector index" in result.stderr
    assert "unexpected-mv" not in result.stderr


def test_remote_deploy_treats_malformed_root_manifest_as_hard_error_before_swap(tmp_path):
    remote = tmp_path / "remote-deploy.sh"
    remote.write_text(_remote_deploy_script(), encoding="utf-8")
    old = _remote_old_tree(tmp_path)
    manifest = old / "feedback_hub" / "data" / "vector_index" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("not-json", encoding="utf-8")
    archive = _archive_for_remote_deploy(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "mv", "#!/bin/sh\necho unexpected-mv >&2\nexit 88\n")

    result = subprocess.run(
        ["bash", str(remote), str(old), str(archive), "8000"],
        env={**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "invalid active vector index" in result.stderr
    assert "unexpected-mv" not in result.stderr


def test_remote_deploy_restores_persisted_state_after_new_directory_is_promoted(tmp_path):
    remote = tmp_path / "remote-deploy.sh"
    remote.write_text(_remote_deploy_script(), encoding="utf-8")
    old = _remote_old_tree(tmp_path)
    archive = _archive_that_fails_after_code_swap(tmp_path)

    result = subprocess.run(["bash", str(remote), str(old), str(archive), "8000"], text=True, capture_output=True)

    assert result.returncode != 0
    assert (old / ".venv" / "state").read_text(encoding="utf-8") == "venv"
    assert (old / ".env").read_text(encoding="utf-8") == "env"
    assert (old / "feedback_hub" / "data" / "state").read_text(encoding="utf-8") == "data"
    assert (old / "scripts" / "devcloud_runtime.sh").read_text(encoding="utf-8") == "#!/bin/sh\nexit 0\n"


def test_remote_deploy_ignores_term_after_commit_before_backup_delete(tmp_path):
    remote = tmp_path / "remote-deploy.sh"
    remote.write_text(_remote_deploy_script(), encoding="utf-8")
    old = _remote_old_tree(tmp_path)
    archive = _archive_that_succeeds_and_arms_signal(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ready = tmp_path / "signal-ready"
    _write_executable(
        fake_bin / "rm",
        "#!/bin/sh\n"
        "for value in \"$@\"; do\n"
        "  if [ \"$value\" = \"${TARGET_BAK}\" ] && [ -e \"${SIGNAL_READY}\" ]; then kill -TERM \"$PPID\"; fi\n"
        "done\n"
        "exec /bin/rm \"$@\"\n",
    )

    result = subprocess.run(
        ["bash", str(remote), str(old), str(archive), "8000"],
        env={
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "TARGET_BAK": f"{old}.bak",
            "SIGNAL_READY": str(ready),
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (old / "new-marker").read_text(encoding="utf-8") == "new"
    assert (old / ".venv" / "state").read_text(encoding="utf-8") == "venv"
    assert not Path(f"{old}.bak").exists()


def test_runtime_exposes_vector_health_coordination_for_deploys():
    body = RUNTIME.read_text(encoding="utf-8")

    assert "vector_index_state" in body
    assert "VECTOR_PORT" in body
    assert "/health" in body
    assert "vector_runtime.sh" in body
    assert "invalid active vector index" in body
    assert '"$VECTOR_RUNTIME" status' in body


@pytest.mark.parametrize("index_state", ["valid", "malformed"])
def test_runtime_status_rejects_unhealthy_or_malformed_active_vector_dependency(tmp_path, index_state):
    app = tmp_path / "app"
    runtime = app / "scripts" / "devcloud_runtime.sh"
    runtime.parent.mkdir(parents=True)
    shutil.copy2(RUNTIME, runtime)
    vector_runtime = app / "scripts" / "vector_runtime.sh"
    _write_executable(vector_runtime, "#!/bin/sh\necho vector-unhealthy >&2\nexit 1\n")
    run_dir = app / "run"
    run_dir.mkdir()
    (run_dir / "feedback_hub.pid").write_text("$$", encoding="utf-8")
    data_dir = app / "feedback_hub" / "data" / "vector_index"
    data_dir.mkdir(parents=True)
    if index_state == "valid":
        (data_dir / "manifest.json").write_text("{}", encoding="utf-8")
    else:
        (data_dir / "active-generation.json").write_text('{"generation_id":"../unsafe"}', encoding="utf-8")

    result = subprocess.run(
        ["bash", "-c", 'DEVCLOUD_RUNTIME_LIBRARY=1 source "$1"; echo $$ > "$PID_FILE"; set +e; status_app', "bash", str(runtime)],
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    if index_state == "valid":
        assert "vector-unhealthy" in result.stderr
    else:
        assert "invalid active vector index" in result.stderr


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


def test_vector_bootstrap_arms_remote_cleanup_before_first_transport_failure(tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    for filename in ("config.json", "tokenizer.json", "model.safetensors"):
        (model_dir / filename).write_text("fixture", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "ssh-calls"
    _write_executable(
        fake_bin / "ssh",
        "#!/bin/sh\n"
        "count=$(cat \"${SSH_CALLS}\" 2>/dev/null || echo 0)\n"
        "count=$((count + 1)); printf '%s' \"$count\" > \"${SSH_CALLS}\"\n"
        "if [ \"$count\" = 1 ]; then exit 42; fi\n"
        "exit 0\n",
    )

    result = subprocess.run(
        ["bash", str(VECTOR_DEPLOY)],
        env={
            **os.environ,
            "MODEL_SOURCE_DIR": str(model_dir),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "SSH_CALLS": str(calls),
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 42
    assert calls.read_text(encoding="utf-8") == "2"


@pytest.mark.parametrize(("failure", "expected_ssh_calls"), [("manifest-upload", "2"), ("remote-prepare", "3")])
def test_vector_bootstrap_cleans_remote_temp_artifacts_after_each_transport_creation_failure(tmp_path, failure, expected_ssh_calls):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    for filename in ("config.json", "tokenizer.json", "model.safetensors"):
        (model_dir / filename).write_text("fixture", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ssh_calls = tmp_path / "ssh-calls"
    scp_calls = tmp_path / "scp-calls"
    _write_executable(
        fake_bin / "ssh",
        "#!/bin/sh\n"
        "count=$(cat \"${SSH_CALLS}\" 2>/dev/null || echo 0)\n"
        "count=$((count + 1)); printf '%s' \"$count\" > \"${SSH_CALLS}\"\n"
        "if [ \"${FAILURE}\" = remote-prepare ] && [ \"$count\" = 2 ]; then exit 45; fi\n"
        "exit 0\n",
    )
    _write_executable(
        fake_bin / "scp",
        "#!/bin/sh\n"
        "count=$(cat \"${SCP_CALLS}\" 2>/dev/null || echo 0)\n"
        "count=$((count + 1)); printf '%s' \"$count\" > \"${SCP_CALLS}\"\n"
        "if [ \"${FAILURE}\" = manifest-upload ] && [ \"$count\" = 1 ]; then exit 44; fi\n"
        "exit 0\n",
    )

    result = subprocess.run(
        ["bash", str(VECTOR_DEPLOY)],
        env={
            **os.environ,
            "MODEL_SOURCE_DIR": str(model_dir),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "SSH_CALLS": str(ssh_calls),
            "SCP_CALLS": str(scp_calls),
            "FAILURE": failure,
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode in (44, 45)
    assert ssh_calls.read_text(encoding="utf-8") == expected_ssh_calls


def test_vector_deploy_runbook_documents_bootstrap_start_health_and_bounded_rollback():
    body = RUNBOOK.read_text(encoding="utf-8")

    for value in (
        "/opt/feedback_hub/feedback_hub/data/models/Qwen3-Embedding-0.6B",
        "127.0.0.1:8011",
        "TOPIC_MINING_API_TOKEN",
        "--last 14d",
        "crontab-pre-vector.txt",
        "active-generation.json",
        "vector_index/backups/active-generation.json.pre-rebuild",
        "test -s \"$ACTIVE_POINTER_BACKUP\"",
        "scripts/vector_runtime.sh stop",
    ):
        assert value in body


def test_vector_deploy_scripts_parse_without_contacting_remote_hosts():
    for script in (VECTOR_DEPLOY, REGULAR_DEPLOY, RUNTIME):
        result = subprocess.run(["bash", "-n", str(script)], text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
