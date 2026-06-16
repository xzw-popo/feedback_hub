"""Search report API endpoints."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from feedback_hub.search.report_service import (
    CreateReportPayload,
    create_report_job,
    get_report_job,
    list_report_jobs,
)

router = APIRouter(prefix="/api/search-reports", tags=["search-reports"])


class CreateSearchReportRequest(BaseModel):
    title: str = "搜索反馈分析报告"
    query: Optional[str] = None
    search_type: str = "smart"
    filters: dict[str, Any] = Field(default_factory=dict)
    search_payload: dict[str, Any] = Field(default_factory=dict)
    conversation_ids: list[str]
    ai_scores: dict[str, Any] = Field(default_factory=dict)


@router.post("")
def create_search_report(req: CreateSearchReportRequest):
    try:
        return create_report_job(
            CreateReportPayload(
                title=req.title,
                query=req.query,
                search_type=req.search_type,
                filters=req.filters,
                search_payload=req.search_payload,
                conversation_ids=req.conversation_ids,
                ai_scores=req.ai_scores,
            )
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("")
def list_search_reports(limit: int = Query(20, ge=1, le=100)):
    return {"items": list_report_jobs(limit=limit)}


@router.get("/{job_id}")
def get_search_report(job_id: str):
    job = get_report_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="report job not found")
    return job
