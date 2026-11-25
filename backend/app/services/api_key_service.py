from __future__ import annotations

import secrets

from app.core.crypto import generate_id, hash_token, utc_now_iso
from app.core.errors import BadRequestError, NotFoundError
from app.db.base import Database
from app.schemas.auth import ApiKeyResponse, CreateApiKeyRequest, CreateApiKeyResponse
from app.services.audit_service import record_audit

VALID_SCOPES = {"invoices:read", "invoices:write", "invoices:review", "webhooks:manage"}


async def create_api_key(
    db: Database,
    organisation_id: str,
    user_id: str,
    payload: CreateApiKeyRequest,
    request_id: str | None = None,
) -> CreateApiKeyResponse:
    invalid = [s for s in payload.scopes if s not in VALID_SCOPES]
    if invalid:
        raise BadRequestError(f"Unsupported API key scopes: {', '.join(invalid)}")
    scopes = sorted(set(payload.scopes)) or ["invoices:read"]

    raw_secret = f"il_live_{secrets.token_urlsafe(32)}"
    key_prefix = raw_secret[:16]
    key_hash = hash_token(raw_secret)
    key_id = generate_id("key")
    now = utc_now_iso()

    await db.execute(
        """
        INSERT INTO api_keys (
            id, organisation_id, created_by_user_id, name,
            key_prefix, key_hash, scopes, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            key_id,
            organisation_id,
            user_id,
            payload.name.strip(),
            key_prefix,
            key_hash,
            ",".join(scopes),
            now,
        ),
    )
    await record_audit(
        db,
        organisation_id=organisation_id,
        actor_type="user",
        actor_id=user_id,
        action="api_key.created",
        resource_type="api_key",
        resource_id=key_id,
        metadata={"name": payload.name.strip(), "key_prefix": key_prefix, "scopes": scopes},
        request_id=request_id,
    )
    return CreateApiKeyResponse(
        id=key_id,
        name=payload.name.strip(),
        key_prefix=key_prefix,
        scopes=scopes,
        last_used_at=None,
        revoked_at=None,
        created_at=now,
        secret_key=raw_secret,
    )


async def list_api_keys(db: Database, organisation_id: str) -> list[ApiKeyResponse]:
    rows = await db.fetchall(
        """
        SELECT id, name, key_prefix, scopes, last_used_at, revoked_at, created_at
        FROM api_keys
        WHERE organisation_id = ?
        ORDER BY created_at DESC
        """,
        (organisation_id,),
    )
    return [
        ApiKeyResponse(
            id=str(r["id"]),
            name=str(r["name"]),
            key_prefix=str(r["key_prefix"]),
            scopes=[s for s in str(r["scopes"]).split(",") if s],
            last_used_at=r["last_used_at"],
            revoked_at=r["revoked_at"],
            created_at=str(r["created_at"]),
        )
        for r in rows
    ]


async def revoke_api_key(
    db: Database,
    organisation_id: str,
    key_id: str,
    actor_id: str,
    request_id: str | None = None,
) -> None:
    row = await db.fetchone(
        "SELECT id, revoked_at FROM api_keys WHERE id = ? AND organisation_id = ?",
        (key_id, organisation_id),
    )
    if row is None:
        raise NotFoundError("API key not found")
    if row["revoked_at"] is None:
        await db.execute(
            "UPDATE api_keys SET revoked_at = ? WHERE id = ? AND organisation_id = ?",
            (utc_now_iso(), key_id, organisation_id),
        )
        await record_audit(
            db,
            organisation_id=organisation_id,
            actor_type="user",
            actor_id=actor_id,
            action="api_key.revoked",
            resource_type="api_key",
            resource_id=key_id,
            request_id=request_id,
        )
