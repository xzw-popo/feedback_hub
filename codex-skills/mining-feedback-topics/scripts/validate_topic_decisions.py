#!/usr/bin/env python3
"""Validate one complete caller-AI decision page without network access."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


FIELDS = frozenset({"item_id", "label", "reason", "evidence"})


def _load(path: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON file: {Path(path).name}") from exc


def validate(
    page: Any,
    decisions: Any,
    *,
    repair: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(page, dict) or not isinstance(page.get("items"), list):
        raise ValueError("candidate page is invalid")
    if not isinstance(decisions, list):
        raise ValueError("decision file must contain a JSON list")
    items = page["items"]
    if len(items) > 20:
        raise ValueError("candidate page exceeds 20 items")
    by_id: dict[str, dict[str, Any]] = {}
    for row in items:
        if not isinstance(row, dict):
            raise ValueError("candidate page is invalid")
        item_id = row.get("item_id")
        if not isinstance(item_id, str) or not item_id or item_id in by_id:
            raise ValueError("candidate page has invalid item IDs")
        by_id[item_id] = row
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in decisions:
        if not isinstance(raw, dict) or set(raw) != FIELDS:
            raise ValueError("decision has unsupported fields")
        item_id = raw.get("item_id")
        if not isinstance(item_id, str) or item_id not in by_id:
            raise ValueError("decision contains an extra item ID")
        if item_id in seen:
            raise ValueError("decision contains a duplicate item ID")
        seen.add(item_id)
        label = raw.get("label")
        if label not in {"matched", "not_matched"}:
            raise ValueError("decision label is invalid")
        reason = raw.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("decision reason is required")
        evidence = raw.get("evidence")
        if (
            not isinstance(evidence, list)
            or any(not isinstance(value, str) or not value for value in evidence)
        ):
            raise ValueError("decision evidence is invalid")
        if label == "matched" and not evidence:
            raise ValueError("matched decision requires evidence")
        if label == "not_matched" and evidence:
            raise ValueError("not_matched decision cannot include evidence")
        row = by_id[item_id]
        source_item = row.get("item")
        texts = []
        if isinstance(source_item, dict) and isinstance(source_item.get("text"), str):
            texts.append(source_item["text"])
        for context in row.get("context_items", []):
            if isinstance(context, dict):
                text = context.get("text") or context.get("feedback_text")
                if isinstance(text, str):
                    texts.append(text)
        if any(not any(fragment in text for text in texts) for fragment in evidence):
            raise ValueError("decision evidence is not grounded")
        normalized.append({
            "item_id": item_id,
            "label": label,
            "reason": reason.strip(),
            "evidence": list(evidence),
        })
    if repair and not seen:
        raise ValueError("repair decision file must not be empty")
    if not repair and seen != set(by_id):
        raise ValueError("decision file is missing candidate item IDs")
    return normalized


def _write_atomic(path: str, value: Any, inputs: tuple[str, str]) -> None:
    target = Path(path).resolve()
    if target in {Path(value).resolve() for value in inputs}:
        raise ValueError("output must differ from input files")
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise ValueError(f"cannot write output: {target.name}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repair", action="store_true")
    arguments = parser.parse_args()
    try:
        result = validate(
            _load(arguments.page), _load(arguments.file),
            repair=arguments.repair,
        )
        _write_atomic(
            arguments.output, result, (arguments.page, arguments.file),
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps({
        "output": arguments.output,
        "decision_count": len(result),
        "repair": arguments.repair,
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
