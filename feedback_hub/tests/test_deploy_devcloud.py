from __future__ import annotations

import re
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = REPO_ROOT / "deploy_devcloud.sh"
DEPLOY_DOC = REPO_ROOT / "docs" / "devcloud-container-deployment.md"


def _archive_excludes() -> list[str]:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    archive_block = script.split("tar \\\n", 1)[1].split('-czf "$ARCHIVE" .', 1)[0]
    return re.findall(r"--exclude='([^']+)'", archive_block)


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
