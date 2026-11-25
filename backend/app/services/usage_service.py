from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from app.db.base import Database
from app.schemas.usage import ApiRequestLogItem, RecentActivityItem, UsageSummaryResponse


async def get_usage_summary(db: Database, organisation_id: str) -> UsageSummaryResponse:
    counts_rows = await db.fetchall(
        """
        SELECT status, COUNT(*) AS cnt, COALESCE(SUM(COALESCE(page_count, 0)), 0) AS pages
        FROM invoices
        WHERE organisation_id = ? AND deleted_at IS NULL
        GROUP BY status
        """,
        (organisation_id,),
    )
    by_status: dict[str, int] = {}
    total_invoices = 0
    total_pages = 0
    for r in counts_rows:
        cnt = int(r["cnt"])
        by_status[str(r["status"])] = cnt
        total_invoices += cnt
        total_pages += int(r["pages"])

    cutoff_30d = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    req_count_row = await db.fetchone(
        """
        SELECT COUNT(*) AS cnt
        FROM api_request_logs
        WHERE organisation_id = ? AND created_at >= ?
        """,
        (organisation_id, cutoff_30d),
    )
    api_requests_30d = int(req_count_row["cnt"]) if req_count_row else 0

    audit_rows = await db.fetchall(
        """
        SELECT id, action, resource_type, resource_id, actor_type, metadata_json, created_at
        FROM audit_logs
        WHERE organisation_id = ?
        ORDER BY created_at DESC
        LIMIT 15
        """,
        (organisation_id,),
    )
    recent_activity = [
        RecentActivityItem(
            id=str(r["id"]),
            action=str(r["action"]),
            resource_type=str(r["resource_type"]),
            resource_id=r["resource_id"],
            actor_type=str(r["actor_type"]),
            metadata=json.loads(str(r["metadata_json"])) if r["metadata_json"] else None,
            created_at=str(r["created_at"]),
        )
        for r in audit_rows
    ]

    api_log_rows = await db.fetchall(
        """
        SELECT id, api_key_id, request_id, method, path, status_code,
               pages_processed, latency_ms, created_at
        FROM api_request_logs
        WHERE organisation_id = ?
        ORDER BY created_at DESC
        LIMIT 25
        """,
        (organisation_id,),
    )
    recent_api_requests = [
        ApiRequestLogItem(
            id=str(r["id"]),
            api_key_id=r["api_key_id"],
            request_id=str(r["request_id"]),
            method=str(r["method"]),
            path=str(r["path"]),
            status_code=int(r["status_code"]),
            pages_processed=int(r["pages_processed"]),
            latency_ms=int(r["latency_ms"]),
            created_at=str(r["created_at"]),
        )
        for r in api_log_rows
    ]

    return UsageSummaryResponse(
        total_invoices=total_invoices,
        queued_invoices=by_status.get("queued", 0),
        processing_invoices=by_status.get("processing", 0),
        extracted_invoices=by_status.get("extracted", 0),
        needs_review_invoices=by_status.get("needs_review", 0),
        approved_invoices=by_status.get("approved", 0),
        rejected_invoices=by_status.get("rejected", 0),
        failed_invoices=by_status.get("failed", 0),
        total_pages_processed=total_pages,
        api_requests_30d=api_requests_30d,
        recent_activity=recent_activity,
        recent_api_requests=recent_api_requests,
    )
