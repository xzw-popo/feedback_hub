"""Prompt rendering for feedback label v2 experiments."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROMPT_PATH = Path(__file__).with_name("prompt.md")


def build_prompt(
    text: str,
    *,
    metadata: dict[str, Any] | None = None,
    rule_hints: dict[str, Any] | None = None,
) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    metadata_json = json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)
    rule_hints_json = json.dumps(rule_hints or {}, ensure_ascii=False, sort_keys=True)
    return (
        template
        .replace("{{TEXT}}", text or "")
        .replace("{{METADATA}}", metadata_json)
        .replace("{{RULE_HINTS}}", rule_hints_json)
    )
