"""Build an auditable daily-insight shortlist and select final report items."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from typing import Any, Callable, Iterable

from feedback_hub.topic_discovery.model_routes import (
    ModelRoute,
    QuotaExhaustedError,
    call_model_route,
    invoke_model_route,
    normalize_model_routes,
    run_pauseable_model_jobs,
)


DEFAULT_LANE_LIMITS = {
    "rising_or_repeated_bug": 10,
    "new_bug": 8,
    "demand_opportunity": 8,
    "high_value_single": 4,
}
_LANE_ORDER = tuple(DEFAULT_LANE_LIMITS)
_FIX_PRIORITY_PHRASES = (
    "最严重", "修复优先", "优先修复", "修复紧迫", "立即修复", "立即介入", "必须修复",
)
_RETENTION_CLAIMS = ("获客", "留存", "流失风险", "用户流失")
_RETENTION_EVIDENCE = ("转用", "换用", "卸载", "不用了", "竞品", "搜狗", "流失")
_FENCE_JSON_RE = re.compile(r"```json\s*(.+?)\s*```", re.DOTALL | re.IGNORECASE)
_BRACE_RE = re.compile(r"\{[\s\S]*\}")


def enrich_insights_with_evidence(
    insights: list[dict[str, Any]],
    candidates: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach deduplicated conversation, source, and product-context evidence."""
    insight_ids = [str(row.get("insight_id") or "") for row in insights]
    if not all(insight_ids) or len(insight_ids) != len(set(insight_ids)):
        raise ValueError("insight_id must be present and unique")
    rows = []
    for insight in insights:
        source_ids = _dedupe_strings(insight.get("source_candidate_ids") or [])
        if not source_ids or any(candidate_id not in candidates for candidate_id in source_ids):
            raise ValueError("insight references an unknown candidate")
        sources = [candidates[candidate_id] for candidate_id in source_ids]
        baseline_dates = sorted({
            str(day)
            for candidate in sources
            for day in candidate.get("baseline_dates") or []
            if str(day)
        })
        baseline_ids_by_date = {
            day: _dedupe_strings(
                conversation_id
                for candidate in sources
                for conversation_id in (
                    candidate.get("baseline_conversation_ids_by_date") or {}
                ).get(day, [])
            )
            for day in baseline_dates
        }
        representative_units = _dedupe_units(
            unit
            for candidate in sources
            for unit in candidate.get("representative_issue_units") or []
        )
        today_ids = _dedupe_strings(
            conversation_id
            for candidate in sources
            for conversation_id in candidate.get("today_conversation_ids") or []
        )
        baseline_counts = [len(baseline_ids_by_date[day]) for day in baseline_dates]
        row = dict(insight)
        row.update({
            "local_report_decision": str(insight.get("report_decision") or ""),
            "source_candidate_ids": source_ids,
            "source_daily_topic_ids": _dedupe_strings(
                candidate.get("daily_topic_id") for candidate in sources
            ),
            "source_stable_topic_ids": _dedupe_strings(
                candidate.get("stable_topic_id") for candidate in sources
            ),
            "source_topic_titles": _dedupe_strings(
                candidate.get("title") for candidate in sources
            ),
            "source_links": _dedupe_strings(
                link for candidate in sources for link in candidate.get("source_links") or []
            ),
            "today_conversation_ids": today_ids,
            "today_conversation_count": len(today_ids),
            "baseline_dates": baseline_dates,
            "baseline_daily_counts": baseline_counts,
            "baseline_conversation_ids_by_date": baseline_ids_by_date,
            "baseline_active_days": sum(value > 0 for value in baseline_counts),
            "baseline_total_conversations": sum(baseline_counts),
            "representative_issue_units": representative_units,
            "evidence_span_count": sum(
                len(unit.get("evidence_spans") or []) for unit in representative_units
            ),
            "known_context": any(bool(candidate.get("known_context")) for candidate in sources),
            "recall_reasons": _dedupe_strings(
                reason for candidate in sources for reason in candidate.get("recall_reasons") or []
            ),
            "platform_counts": _sum_counts(candidate.get("platform_counts") or {} for candidate in sources),
            "appversion_counts": _sum_counts(candidate.get("appversion_counts") or {} for candidate in sources),
            "feature_candidate_counts": _sum_counts(
                candidate.get("feature_candidate_counts") or {} for candidate in sources
            ),
        })
        rows.append(row)
    return rows


def build_global_shortlist(
    insights: list[dict[str, Any]],
    candidates: dict[str, dict[str, Any]],
    *,
    lane_limits: dict[str, int] | None = None,
    max_items: int = 30,
) -> list[dict[str, Any]]:
    """Recall bounded lane leaders without a cross-lane composite score."""
    if max_items < 0 or max_items > 30:
        raise ValueError("max_items must be between 0 and 30")
    limits = dict(DEFAULT_LANE_LIMITS if lane_limits is None else lane_limits)
    if any(lane not in DEFAULT_LANE_LIMITS for lane in limits):
        raise ValueError("unsupported shortlist lane")
    if any(not isinstance(limit, int) or isinstance(limit, bool) or limit < 0 for limit in limits.values()):
        raise ValueError("shortlist lane limits must be non-negative integers")
    if sum(limits.values()) > max_items:
        raise ValueError("shortlist lane limits exceed max_items")

    enriched = enrich_insights_with_evidence(insights, candidates)
    for row in enriched:
        row.update(_report_priority(row))
    eligible = [
        row for row in enriched
        if row.get("report_decision") not in {"exclude", "manual_review"}
        and not row.get("needs_human_review")
        and row.get("source_links")
        and row.get("signal_type") in limits
    ]
    selected: list[dict[str, Any]] = []
    for lane in _LANE_ORDER:
        limit = limits.get(lane, 0)
        lane_rows = sorted(
            (row for row in eligible if row.get("signal_type") == lane),
            key=lambda row: _lane_sort_key(lane, row),
        )[:limit]
        for lane_rank, row in enumerate(lane_rows, 1):
            selected.append({
                **row,
                "shortlist_lanes": [lane],
                "shortlist_lane_rank": lane_rank,
                "shortlist_rank_fields": _rank_fields(row),
            })
    insight_ids = [str(row["insight_id"]) for row in selected]
    if len(insight_ids) != len(set(insight_ids)) or len(selected) > max_items:
        raise ValueError("global shortlist must be unique and bounded")
    return selected


def build_global_selection_prompt(shortlist: list[dict[str, Any]]) -> str:
    """Build one compact prompt that compares the complete shortlist."""
    compact = [_compact_shortlist_row(row) for row in shortlist]
    return "\n".join([
        "GLOBAL DAILY INSIGHT SELECTION",
        "You are the final editor for a WeType user-feedback daily report.",
        "You must compare every shortlist item and select zero to five insights that most deserve proactive product attention today.",
        "There is no fixed type quota. Do not select an item merely to create category diversity or fill five slots.",
        "Compare product impact, actionability, evidence quality, today's information gain, user task, and duplication with other items.",
        "Frequency is evidence but is not a complete importance score. Local nominate is not final inclusion, and a strong observe may be selected.",
        "Do not cite model confidence in selection_reason or report_summary; it is internal routing evidence, not a product conclusion.",
        "This stage selects report attention and does not decide fix priority, severity, or implementation urgency. Phrase reasons as why an item deserves attention today.",
        "Do not claim acquisition, retention, or churn impact unless a supplied evidence span explicitly says the user switched, uninstalled, stopped using, or chose a competitor.",
        "Treat low report_priority as a product-policy warning. Skin, visual-theme, and account-login preferences should not be selected merely for frequency; select them only for clear breakage or unusual new evidence.",
        "You must not change headline, signal_type, or trend_claim, and you must not add facts, root causes, severity, or population impact.",
        "Do not select needs_human_review items, linkless items, or two insights that overlap on source_candidate_ids.",
        "Use only supplied insight_id values. Return exactly one JSON object with no Markdown or extra text.",
        "selection_reason must explain why the item wins relative to the other supplied candidates, not only why it is valid in isolation.",
        "report_summary must restate only the supplied issue facts and must omit frequency numbers because deterministic counts are rendered separately.",
        "Keep selection_reason under 120 Chinese characters and editorial_note under 80 Chinese characters.",
        "",
        "```json",
        json.dumps(compact, ensure_ascii=False, sort_keys=True),
        "```",
        "",
        "Return shape:",
        json.dumps({
            "selected_insights": [{
                "insight_id": "supplied insight id",
                "rank": 1,
                "selection_reason": "why this is more important today than other shortlist items",
                "editorial_note": "optional evidence boundary or wording note",
                "report_summary": "fact-only issue summary without frequency numbers",
            }],
            "selection_summary": "overall tradeoff for today's selection",
        }, ensure_ascii=False),
    ])


def parse_global_selection_reply(
    reply: str,
    shortlist: list[dict[str, Any]],
) -> dict[str, Any]:
    """Parse and validate a ranked zero-to-five global selection."""
    payload = _load_json_object(reply)
    values = payload.get("selected_insights")
    if not isinstance(values, list) or len(values) > 5:
        raise ValueError("selected_insights must contain zero to five items")
    if any(not isinstance(value, dict) for value in values):
        raise ValueError("selected insight must be an object")
    allowed = {str(row.get("insight_id") or ""): row for row in shortlist}
    if not all(allowed) or len(allowed) != len(shortlist):
        raise ValueError("shortlist insight_id must be present and unique")
    insight_ids = [str(value.get("insight_id") or "") for value in values]
    if len(insight_ids) != len(set(insight_ids)):
        raise ValueError("duplicate selected insight")
    if any(insight_id not in allowed for insight_id in insight_ids):
        raise ValueError("unknown insight in global selection")

    normalized = []
    for value in values:
        insight_id = str(value["insight_id"])
        rank = value.get("rank")
        if not isinstance(rank, int) or isinstance(rank, bool):
            raise ValueError("global selection rank must be an integer")
        source = allowed[insight_id]
        if source.get("needs_human_review"):
            raise ValueError("selected insight requires human review")
        if not source.get("source_links"):
            raise ValueError("selected insight requires a source link")
        reason = str(value.get("selection_reason") or "").strip()
        if not reason:
            raise ValueError("global selection reason is required")
        if "置信度" in reason or "confidence" in reason.lower():
            raise ValueError("global selection reason cannot cite model confidence")
        if any(phrase in reason for phrase in _FIX_PRIORITY_PHRASES):
            raise ValueError("global selection reason cannot decide fix priority")
        if any(claim in reason for claim in _RETENTION_CLAIMS):
            evidence_text = _selection_evidence_text(source)
            if not any(marker in evidence_text for marker in _RETENTION_EVIDENCE):
                raise ValueError("global selection reason lacks direct retention evidence")
        report_summary = str(value.get("report_summary") or "").strip()
        if not report_summary:
            raise ValueError("global report summary is required")
        normalized.append({
            "insight_id": insight_id,
            "rank": rank,
            "selection_reason": reason[:500],
            "editorial_note": str(value.get("editorial_note") or "").strip()[:300],
            "report_summary": report_summary[:500],
        })
    normalized.sort(key=lambda row: row["rank"])
    if [row["rank"] for row in normalized] != list(range(1, len(normalized) + 1)):
        raise ValueError("global selection ranks must be consecutive")
    _validate_no_source_overlap([row["insight_id"] for row in normalized], allowed)
    return {
        "selected_insights": normalized,
        "selection_summary": str(payload.get("selection_summary") or "").strip()[:1000],
    }


def run_global_selection_multi_channel(
    shortlist: list[dict[str, Any]],
    *,
    routes: list[Any],
    output_path: str | Path,
    max_retries: int = 4,
    request_timeout: int = 300,
    resume: bool = False,
    call_fn: Callable[..., Any] = call_model_route,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run one strict global-selection job through the resumable scheduler."""
    if request_timeout < 1:
        raise ValueError("request_timeout must be positive")
    output_path = Path(output_path)
    if not shortlist:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("", encoding="utf-8")
        return {
            "selected_insights": [],
            "selection_summary": "No eligible shortlist items.",
        }, {
            "run_status": "completed",
            "call_failed": 0,
            "parse_failed": 0,
            "route_counts": {},
            "processed": 0,
            "resumed": 0,
            "remaining": 0,
        }
    normalized_routes = normalize_model_routes(routes)

    def worker(_index: int, key: str, payload: dict[str, Any], route: ModelRoute):
        raw_replies: list[str] = []
        attempts = 0
        parse_attempts = 0
        elapsed_ms = 0
        retry_chain: list[str] = []
        selection: dict[str, Any] = {}
        parse_error = None
        call_error = None
        try:
            for _attempt in range(2):
                reply = invoke_model_route(
                    build_global_selection_prompt(payload["shortlist"]),
                    route=route,
                    call_fn=call_fn,
                    max_retries=max_retries,
                    timeout=request_timeout,
                )
                raw_replies.append(reply.content)
                attempts += reply.attempts
                parse_attempts += 1
                elapsed_ms += reply.elapsed_ms
                retry_chain.extend(reply.retry_chain)
                try:
                    selection = parse_global_selection_reply(reply.content, payload["shortlist"])
                    parse_error = None
                    break
                except ValueError as exc:
                    parse_error = f"{type(exc).__name__}: {exc}"
        except QuotaExhaustedError:
            raise
        except Exception as exc:
            call_error = f"{type(exc).__name__}: {exc}"
        error = call_error or parse_error
        row = {
            "batch_key": key,
            "selection": selection,
            "selection_raw_reply": raw_replies[-1] if raw_replies else "",
            "selection_error": call_error,
            "selection_parse_error": parse_error,
            "parse_status": "ok" if error is None else "failed",
            "selection_parse_attempts": parse_attempts,
            "route_source": route.name,
            "endpoint_class": route.endpoint_class,
            "model": route.model or "agent_default",
            "prompt_version": "daily_insight_global_selection_v2_evidence_boundaries",
            "attempts": attempts,
            "retry_chain": retry_chain,
            "elapsed_ms": elapsed_ms,
        }
        return row, error

    rows, scheduler_stats = run_pauseable_model_jobs(
        [(0, "daily-insight-global-selection", {"shortlist": shortlist})],
        worker,
        routes=normalized_routes,
        output_path=output_path,
        concurrency_per_route=1,
        resume=resume,
    )
    selection = rows[0].get("selection") if rows else {}
    stats = {
        "run_status": scheduler_stats["run_status"],
        "call_failed": sum(bool(row.get("selection_error")) for row in rows),
        "parse_failed": sum(bool(row.get("selection_parse_error")) for row in rows),
        "route_counts": dict(scheduler_stats["route_counts"]),
        "processed": scheduler_stats["processed"],
        "resumed": scheduler_stats["resumed"],
        "remaining": scheduler_stats["remaining"],
        "retryable_failed": scheduler_stats["failed"],
    }
    if scheduler_stats["run_status"] == "paused_quota_exhausted":
        stats.update({
            "quota_route": scheduler_stats["quota_route"],
            "quota_status_code": scheduler_stats["quota_status_code"],
        })
    return selection or {}, stats


def _lane_sort_key(lane: str, row: dict[str, Any]) -> tuple[Any, ...]:
    nominate_tie_break = 0 if row.get("report_decision") == "nominate" else 1
    identity = str(row.get("insight_id") or "")
    if lane == "rising_or_repeated_bug":
        values = (
            int(row.get("today_conversation_count") or 0),
            int(row.get("baseline_active_days") or 0),
            int(row.get("baseline_total_conversations") or 0),
            float(row.get("confidence") or 0.0),
        )
    elif lane == "new_bug":
        values = (
            int(row.get("today_conversation_count") or 0),
            int(row.get("evidence_span_count") or 0),
            int(bool(row.get("known_context"))),
            len(row.get("source_links") or []),
            float(row.get("confidence") or 0.0),
        )
    elif lane == "demand_opportunity":
        values = (
            int(row.get("today_conversation_count") or 0),
            int(row.get("baseline_active_days") or 0),
            int(row.get("baseline_total_conversations") or 0),
            float(row.get("confidence") or 0.0),
        )
    else:
        values = (
            int(row.get("evidence_span_count") or 0),
            int(bool(row.get("known_context"))),
            len(row.get("source_links") or []),
            float(row.get("confidence") or 0.0),
        )
    return tuple(-value for value in values) + (nominate_tie_break, identity)


def _rank_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "today_conversation_count": int(row.get("today_conversation_count") or 0),
        "baseline_active_days": int(row.get("baseline_active_days") or 0),
        "baseline_total_conversations": int(row.get("baseline_total_conversations") or 0),
        "evidence_span_count": int(row.get("evidence_span_count") or 0),
        "known_context": bool(row.get("known_context")),
        "source_link_count": len(row.get("source_links") or []),
        "confidence": float(row.get("confidence") or 0.0),
        "local_report_decision": str(row.get("report_decision") or ""),
    }


def _compact_shortlist_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "insight_id": row.get("insight_id"),
        "local_report_decision": row.get("local_report_decision") or row.get("report_decision"),
        "signal_type": row.get("signal_type"),
        "headline": row.get("headline"),
        "summary": row.get("summary"),
        "local_selection_reason": row.get("selection_reason"),
        "trend_claim": row.get("trend_claim"),
        "today_conversation_count": row.get("today_conversation_count"),
        "baseline_daily_counts": row.get("baseline_daily_counts") or [],
        "baseline_active_days": row.get("baseline_active_days"),
        "platform_counts": row.get("platform_counts") or {},
        "feature_candidate_counts": row.get("feature_candidate_counts") or {},
        "source_candidate_ids": row.get("source_candidate_ids") or [],
        "source_link_count": len(row.get("source_links") or []),
        "representative_evidence": [
            {
                "issue_unit_id": unit.get("issue_unit_id"),
                "summary": unit.get("summary"),
                "evidence_spans": (unit.get("evidence_spans") or [])[:2],
            }
            for unit in (row.get("representative_issue_units") or [])[:3]
        ],
        "confidence": row.get("confidence"),
        "needs_human_review": bool(row.get("needs_human_review")),
        "shortlist_lanes": row.get("shortlist_lanes") or [],
        "shortlist_lane_rank": row.get("shortlist_lane_rank"),
        "shortlist_rank_fields": row.get("shortlist_rank_fields") or {},
        "report_priority": row.get("report_priority") or "normal",
        "report_priority_reasons": row.get("report_priority_reasons") or [],
    }


def _report_priority(row: dict[str, Any]) -> dict[str, Any]:
    text = " ".join(_dedupe_strings([
        row.get("headline"),
        row.get("summary"),
        *(row.get("source_topic_titles") or []),
        *(unit.get("summary") for unit in row.get("representative_issue_units") or []),
    ])).lower()
    reasons = []
    if any(term in text for term in (
        "皮肤", "皮膚", "皮肤商城", "主题商城", "外观选择", "外观定制", "个性主题",
    )):
        reasons.append("other_low_priority:skin_visual_customization")
    if any(term in text for term in ("账号登录", "帳號登錄", "登录账号", "登陆账号")):
        reasons.append("other_low_priority:account_login")
    return {
        "report_priority": "low" if reasons else "normal",
        "report_priority_reasons": reasons,
    }


def _selection_evidence_text(row: dict[str, Any]) -> str:
    values = [row.get("headline"), row.get("summary")]
    for unit in row.get("representative_issue_units") or row.get("representative_evidence") or []:
        values.append(unit.get("summary"))
        values.extend(unit.get("evidence_spans") or [])
    return " ".join(_dedupe_strings(values))


def _validate_no_source_overlap(
    selected_ids: list[str],
    allowed: dict[str, dict[str, Any]],
) -> None:
    seen: set[str] = set()
    for insight_id in selected_ids:
        source_ids = set(_dedupe_strings(allowed[insight_id].get("source_candidate_ids") or []))
        if seen.intersection(source_ids):
            raise ValueError("selected insights overlap on source candidates")
        seen.update(source_ids)


def _load_json_object(reply: str) -> dict[str, Any]:
    fenced = _FENCE_JSON_RE.search(reply)
    raw = fenced.group(1).strip() if fenced else None
    if raw is None:
        braced = _BRACE_RE.search(reply)
        raw = braced.group(0).strip() if braced else None
    if raw is None:
        raise ValueError("no JSON found in global selection reply")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid global selection JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("global selection reply must be an object")
    return payload


def _dedupe_units(values: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    rows = []
    for value in values:
        issue_unit_id = str(value.get("issue_unit_id") or "").strip()
        key = issue_unit_id or json.dumps(value, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            rows.append(dict(value))
    return rows


def _dedupe_strings(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    rows = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            rows.append(text)
    return rows


def _sum_counts(values: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for value in values:
        for key, count in value.items():
            counts[str(key)] += int(count)
    return dict(sorted(counts.items()))
