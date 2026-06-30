"""Offline JSONL experiment runner for feedback label v2."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from feedback_hub.tagger.v2.parser import parse_label_reply
from feedback_hub.tagger.v2.prompting import build_prompt
from feedback_hub.tagger.v2.rule_hints import build_rule_hints

LLMCall = Callable[[str], str]


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if not isinstance(obj, dict):
                raise ValueError(f"line {line_no} is not a JSON object")
            yield obj


def _metadata(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item[key]
        for key in ("platform", "appversion", "channel", "device", "source", "ts_ms")
        if key in item and item[key] is not None
    }


def run_jsonl_experiment(
    input_path: str | Path,
    output_path: str | Path,
    *,
    llm_call: LLMCall,
    limit: int | None = None,
) -> dict[str, int]:
    input_path = Path(input_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    stats = {"total": 0, "labeled": 0, "failed": 0, "skipped_by_rule": 0}
    with output_path.open("w", encoding="utf-8") as out:
        for item in _iter_jsonl(input_path):
            if limit is not None and stats["total"] >= limit:
                break
            stats["total"] += 1
            text = str(item.get("text") or "")
            metadata = _metadata(item)
            rule_hints = build_rule_hints(text, metadata=metadata)
            if rule_hints.get("skip_llm") and rule_hints.get("direct_label"):
                raw_reply = ""
                label = rule_hints["direct_label"]
                stats["labeled"] += 1
                stats["skipped_by_rule"] += 1
                error = None
            else:
                prompt = build_prompt(text, metadata=metadata, rule_hints=rule_hints)
                try:
                    raw_reply = llm_call(prompt)
                    label = parse_label_reply(raw_reply)
                    stats["labeled"] += 1
                    error = None
                except Exception as exc:
                    raw_reply = ""
                    label = parse_label_reply("")
                    stats["failed"] += 1
                    error = f"{type(exc).__name__}: {exc}"
            row = {
                "feedback_id": item.get("feedback_id"),
                "text": text,
                "metadata": metadata,
                "rule_hints": rule_hints,
                "label": label,
                "raw_reply": raw_reply,
                "error": error,
            }
            out.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run offline feedback label v2 JSONL experiment.")
    parser.add_argument("--input", required=True, help="Input JSONL with feedback_id and text fields.")
    parser.add_argument("--output", required=True, help="Output JSONL path.")
    parser.add_argument("--limit", type=int, default=None)
    parser.parse_args(argv)
    raise SystemExit(
        "CLI LLM provider wiring is intentionally not enabled in the first slice; "
        "use run_jsonl_experiment with an injected llm_call."
    )


if __name__ == "__main__":
    raise SystemExit(main())
