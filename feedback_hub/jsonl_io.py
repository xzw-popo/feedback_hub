"""Shared LF-delimited JSONL parsing for Feedback Hub artifacts."""
from __future__ import annotations

import json
from typing import Any


def jsonl_text_records(raw: bytes) -> tuple[str, ...]:
    text = raw.decode("utf-8")
    return tuple(record for record in text.split("\n") if record.strip())


def load_jsonl_objects(raw: bytes) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for record in jsonl_text_records(raw):
        value = json.loads(record)
        if not isinstance(value, dict):
            raise ValueError("JSONL records must be objects")
        values.append(value)
    return values
