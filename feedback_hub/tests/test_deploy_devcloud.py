from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = REPO_ROOT / "deploy_devcloud.sh"
DEPLOY_DOC = REPO_ROOT / "docs" / "devcloud-container-deployment.md"


def _archive_excludes() -> list[str]:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    archive_block = script.split("tar \\\n", 1)[1].split('-czf "$ARCHIVE" .', 1)[0]
    return re.findall(r"--exclude='([^']+)'", archive_block)


def _topic_mining_doc_section() -> str:
    docs = DEPLOY_DOC.read_text(encoding="utf-8")
    return docs.split("### 5.1 部署一次性反馈专题挖掘后端", 1)[1].split("\n## 6.", 1)[0]


def _topic_health_check_script(project: Path) -> str:
    section = _topic_mining_doc_section()
    marker = "TOPIC_CAPABILITIES_URL="
    marker_index = section.index(marker)
    block_start = section.rfind("```bash\n", 0, marker_index) + len("```bash\n")
    block_end = section.index("\n```", marker_index)
    return section[block_start:block_end].replace(
        "cd /opt/feedback_hub", f"cd {shlex.quote(str(project))}", 1,
    )


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def test_devcloud_archive_explicitly_excludes_distributable_skills():
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "--exclude='codex-skills'" in script


def test_devcloud_archive_excludes_skill_fixture_but_keeps_backend(tmp_path):
    project = tmp_path / "project"
    skill_marker = project / "codex-skills" / "marker.txt"
    backend_marker = project / "feedback_hub" / "marker.txt"
    skill_marker.parent.mkdir(parents=True)
    backend_marker.parent.mkdir(parents=True)
    skill_marker.write_text("skill", encoding="utf-8")
    backend_marker.write_text("backend", encoding="utf-8")
    archive = tmp_path / "fixture.tar.gz"

    subprocess.run(
        ["tar", *(f"--exclude={value}" for value in _archive_excludes()), "-czf", str(archive), "."],
        cwd=project,
        check=True,
    )
    entries = set(subprocess.check_output(["tar", "-tzf", str(archive)], text=True).splitlines())

    assert "./codex-skills/marker.txt" not in entries
    assert "./feedback_hub/marker.txt" in entries


def test_topic_mining_deployment_docs_require_shared_llm_runtime_configuration():
    docs = DEPLOY_DOC.read_text(encoding="utf-8")
    for variable in ("LLM_API_URL", "LLM_API_KEY", "LLM_MODEL"):
        assert variable in docs
    assert "分类必需" in docs


def test_topic_mining_health_check_runs_after_restart_and_handles_env_token_branches():
    section = _topic_mining_doc_section()
    restart = section.index("APP_PORT=8000 scripts/devcloud_runtime.sh restart")
    status = section.index("APP_PORT=8000 scripts/devcloud_runtime.sh status")
    capabilities = section.index("/api/topic-mining/capabilities")
    assert restart < status < capabilities

    assert 'TOPIC_TOKEN="$(' in section
    assert "env -u TOPIC_MINING_API_TOKEN ./.venv/bin/python -c" in section
    assert "from feedback_hub import config" in section
    assert 'os.environ.get("TOPIC_MINING_API_TOKEN", "")' in section

    branch = section.split('if [ -z "$TOPIC_TOKEN" ]; then', 1)[1]
    empty_token_branch, authenticated_branch = branch.split("\nelse\n", 1)
    authenticated_branch = authenticated_branch.split("\nfi", 1)[0]
    assert "TOPIC_CAPABILITIES_URL" in empty_token_branch
    assert '= "200"' in empty_token_branch
    assert authenticated_branch.index('= "401"') < authenticated_branch.index("Authorization: Bearer $TOPIC_TOKEN")
    assert authenticated_branch.index("Authorization: Bearer $TOPIC_TOKEN") < authenticated_branch.rindex('= "200"')


@pytest.mark.parametrize(("token", "unauth_status", "auth_status", "unauth_exit", "auth_exit", "expected_success"), [
    ("", "200", "200", "0", "0", True),
    ("", "503", "200", "0", "0", False),
    ("fixture-token", "401", "200", "0", "0", True),
    ("fixture-token", "200", "200", "0", "0", False),
    ("fixture-token", "401", "503", "0", "0", False),
    # Even an expected-looking body must not hide a curl transport failure.
    ("", "200", "200", "7", "0", False),
    ("fixture-token", "401", "200", "0", "7", False),
])
def test_documented_topic_health_check_propagates_every_failure(
    tmp_path, token, unauth_status, auth_status, unauth_exit, auth_exit, expected_success,
):
    project = tmp_path / "feedback-hub"
    fake_bin = tmp_path / "bin"
    _write_executable(
        project / ".venv" / "bin" / "python",
        "#!/bin/sh\nprintf '%s\\n' \"${FAKE_TOPIC_TOKEN:-}\"\n",
    )
    _write_executable(
        fake_bin / "curl",
        """#!/bin/sh
authenticated=0
for argument in "$@"; do
  if [ "$argument" = "Authorization: Bearer ${FAKE_TOPIC_TOKEN:-}" ]; then
    authenticated=1
  fi
done
if [ "$authenticated" = "1" ]; then
  printf '%s' "${STUB_AUTH_STATUS:-200}"
  exit "${STUB_AUTH_EXIT:-0}"
fi
printf '%s' "${STUB_UNAUTH_STATUS:-200}"
exit "${STUB_UNAUTH_EXIT:-0}"
""",
    )
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "FAKE_TOPIC_TOKEN": token,
        "STUB_UNAUTH_STATUS": unauth_status,
        "STUB_AUTH_STATUS": auth_status,
        "STUB_UNAUTH_EXIT": unauth_exit,
        "STUB_AUTH_EXIT": auth_exit,
        # Prove the documented env -u path ignores an inherited stale value.
        "TOPIC_MINING_API_TOKEN": "stale-shell-token",
    }

    completed = subprocess.run(
        ["bash", "-c", _topic_health_check_script(project)],
        env=environment,
        text=True,
        capture_output=True,
    )

    assert (completed.returncode == 0) is expected_success
    if token:
        assert token not in completed.stdout
        assert token not in completed.stderr
