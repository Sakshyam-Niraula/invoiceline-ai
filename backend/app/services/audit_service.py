from __future__ import annotations

import json
from typing import Any

from app.core.crypto import generate_id, utc_now_iso
from app.db.base import Database, Statement


def build_audit_statement(
    *,
    organisation_id: str,
    actor_type: str,
    actor_id: str | None,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    ip_address: str | None = None,
    request_id: str | None = None,
) -> Statement:
    return Statement(
        sql="""
        INSERT INTO audit_logs (
            id, organisation_id, actor_type, actor_id, action,
            resource_type, resource_id, metadata_json, ip_address, request_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        params=(
            generate_id("aud"),
            organisation_id,
            actor_type,
            actor_id,
            action,
            resource_type,
            resource_id,
            json.dumps(metadata) if metadata is not None else None,
            ip_address,
            request_id,
            utc_now_iso(),
        ),
    )


async def record_audit(
    db: Database,
    *,
    organisation_id: str,
    actor_type: str,
    actor_id: str | None,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    ip_address: str | None = None,
    request_id: str | None = None,
) -> None:
    stmt = build_audit_statement(
        organisation_id=organisation_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata=metadata,
        ip_address=ip_address,
        request_id=request_id,
    )
    await db.execute(stmt.sql, stmt.params)
