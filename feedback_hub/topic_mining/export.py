"""Deterministic and formula-safe exports for already verified topic runs."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping, Sequence

from .run_store import TopicRunStore
from .service import RunVerificationError, _effective_data_cutoff, _load_manifest, _publish_terminal_mutation, _require_run, _validate_final_rows, _verify_manifest, read_verified_artifact_bytes
from .contracts import validate_topic_spec


HEADERS = ["命中分类", "反馈时间", "反馈原文", "对应链接", "判定理由", "证据", "平台", "版本", "设备", "Feedback ID", "Conversation ID", "Run ID", "数据截止时间"]


def export_topic_run(run_id: str, export_format: str, *, store: TopicRunStore | None = None) -> Path:
    if export_format not in {"xlsx", "jsonl"}:
        raise ValueError("unsupported export format")
    if store is None:
        from .service import default_store
        store = default_store()
    run = _require_run(run_id, store)
    if run["status"] != "verified":
        raise RunVerificationError("run_not_verified")
    artifact_dir = Path(run["artifact_dir"])
    spec = validate_topic_spec(json.loads(run["spec_json"]))
    final_path = artifact_dir / "final_reviewed.jsonl"
    manifest = _load_manifest(run, artifact_dir)
    _verify_manifest(manifest, artifact_dir)
    try:
        rows = [json.loads(line) for line in read_verified_artifact_bytes(manifest, final_path).decode("utf-8").splitlines() if line.strip()]
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise RunVerificationError("invalid_artifact") from exc
    data_cutoff_ms = _effective_data_cutoff(run, manifest)
    _validate_final_rows(
        rows, spec, expected_run_id=run_id,
        expected_data_cutoff_ms=data_cutoff_ms,
    )
    rows = sorted(rows, key=lambda row: (str(row["label"]), -int(row["source_item"].get("ts_ms", 0)), str(row["item_id"])))
    jsonl_path = artifact_dir / "final_results.jsonl"
    jsonl_bytes = "".join(
        json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n"
        for row in rows
    ).encode("utf-8")
    report = {
        "run_id": run_id, "status": "verified", "matched_count": len(rows),
        "artifact": jsonl_path.name, "data_cutoff_ms": data_cutoff_ms,
        "data_cutoff_time": _format_time(data_cutoff_ms),
    }
    report_bytes = (
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    files = {
        jsonl_path.name: jsonl_bytes,
        "quality_report.json": report_bytes,
    }
    if export_format == "jsonl":
        _publish_terminal_mutation(
            run, store, manifest, stage="verified", status="verified",
            files=files,
        )
        return jsonl_path
    workbook_path = artifact_dir / "feedback_list.xlsx"
    files[workbook_path.name] = _xlsx_bytes(rows)
    _publish_terminal_mutation(
        run, store, manifest, stage="verified", status="verified",
        files=files,
    )
    return workbook_path


def _write_xlsx(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.write_bytes(_xlsx_bytes(rows))


def _xlsx_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "反馈清单"
    sheet.append(HEADERS)
    header_fill = PatternFill("solid", fgColor="1F4E78")
    for cell in sheet[1]:
        cell.font = Font(name="Aptos", bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for row in rows:
        item = row["source_item"]
        url = str(item["source_url"])
        values = [
            row["label"], _format_time(item.get("ts_ms")), item["text"], _safe_cell(url), row["reason"],
            "\n".join(row["evidence"]), item.get("platform", ""), item.get("appversion", ""),
            item.get("device_name", ""), item.get("feedback_id", ""), item.get("conversation_id", ""), row["run_id"],
            _format_time(row["data_cutoff_ms"]),
        ]
        sheet.append([_safe_cell(value) for value in values])
        link = sheet.cell(sheet.max_row, 4)
        link.hyperlink = url
        link.style = "Hyperlink"
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:M{max(sheet.max_row, 1)}"
    widths = [14, 20, 48, 42, 28, 32, 12, 16, 20, 18, 20, 18, 28]
    for index, width in enumerate(widths, 1):
        sheet.column_dimensions[openpyxl.utils.get_column_letter(index)].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.font = Font(name="Aptos", size=10)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _safe_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@")) else text


def _format_time(value: Any) -> str:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return ""
