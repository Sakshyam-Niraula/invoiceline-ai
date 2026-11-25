from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class RecentActivityItem(BaseModel):
    id: str
    action: str
    resource_type: str
    resource_id: str | None = None
    actor_type: str
    metadata: dict[str, Any] | None = None
    created_at: str


class ApiRequestLogItem(BaseModel):
    id: str
    api_key_id: str | None = None
    request_id: str
    method: str
    path: str
    status_code: int
    pages_processed: int
    latency_ms: int
    created_at: str


class UsageSummaryResponse(BaseModel):
    total_invoices: int
    queued_invoices: int
    processing_invoices: int
    extracted_invoices: int
    needs_review_invoices: int
    approved_invoices: int
    rejected_invoices: int
    failed_invoices: int
    total_pages_processed: int
    api_requests_30d: int
    recent_activity: list[RecentActivityItem]
    recent_api_requests: list[ApiRequestLogItem]
