from __future__ import annotations

import json
import secrets
import time

import httpx

from app.config import Settings
from app.core.crypto import (
    decrypt_secret,
    encrypt_secret,
    generate_id,
    sign_webhook_payload,
    utc_now_iso,
)
from app.core.errors import NotFoundError
from app.db.base import Database
from app.schemas.webhooks import (
    CreateWebhookRequest,
    CreateWebhookResponse,
    WebhookDeliveryResponse,
    WebhookResponse,
)
from app.services.audit_service import record_audit


async def create_webhook(
    db: Database,
    settings: Settings,
    organisation_id: str,
    actor_id: str | None,
    payload: CreateWebhookRequest,
    request_id: str | None = None,
) -> CreateWebhookResponse:
    webhook_id = generate_id("whk")
    raw_secret = f"whsec_{secrets.token_urlsafe(32)}"
    secret_prefix = raw_secret[:12]
    encrypted = encrypt_secret(raw_secret, settings.app_secret)
    events = sorted(set(payload.events))
    now = utc_now_iso()

    await db.execute(
        """
        INSERT INTO webhooks (
            id, organisation_id, url, signing_secret_enc, secret_prefix,
            events_json, is_active, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            webhook_id,
            organisation_id,
            payload.url,
            encrypted,
            secret_prefix,
            json.dumps(events),
            now,
            now,
        ),
    )
    await record_audit(
        db,
        organisation_id=organisation_id,
        actor_type="user",
        actor_id=actor_id,
        action="webhook.created",
        resource_type="webhook",
        resource_id=webhook_id,
        metadata={"url": payload.url, "events": events},
        request_id=request_id,
    )
    return CreateWebhookResponse(
        id=webhook_id,
        url=payload.url,
        secret_prefix=secret_prefix,
        events=events,
        is_active=True,
        created_at=now,
        updated_at=now,
        signing_secret=raw_secret,
    )


async def list_webhooks(db: Database, organisation_id: str) -> list[WebhookResponse]:
    rows = await db.fetchall(
        """
        SELECT id, url, secret_prefix, events_json, is_active, created_at, updated_at
        FROM webhooks
        WHERE organisation_id = ?
        ORDER BY created_at DESC
        """,
        (organisation_id,),
    )
    return [
        WebhookResponse(
            id=str(r["id"]),
            url=str(r["url"]),
            secret_prefix=str(r["secret_prefix"]),
            events=json.loads(str(r["events_json"])),
            is_active=bool(r["is_active"]),
            created_at=str(r["created_at"]),
            updated_at=str(r["updated_at"]),
        )
        for r in rows
    ]


async def delete_webhook(
    db: Database,
    organisation_id: str,
    webhook_id: str,
    actor_id: str | None,
    request_id: str | None = None,
) -> None:
    row = await db.fetchone(
        "SELECT id FROM webhooks WHERE id = ? AND organisation_id = ?",
        (webhook_id, organisation_id),
    )
    if row is None:
        raise NotFoundError("Webhook not found")
    await db.execute(
        "DELETE FROM webhooks WHERE id = ? AND organisation_id = ?",
        (webhook_id, organisation_id),
    )
    await record_audit(
        db,
        organisation_id=organisation_id,
        actor_type="user",
        actor_id=actor_id,
        action="webhook.deleted",
        resource_type="webhook",
        resource_id=webhook_id,
        request_id=request_id,
    )


async def list_webhook_deliveries(
    db: Database,
    organisation_id: str,
    limit: int = 50,
) -> list[WebhookDeliveryResponse]:
    rows = await db.fetchall(
        """
        SELECT id, webhook_id, invoice_id, event_type, request_id,
               response_status, response_body, attempt_count, status, delivered_at, created_at
        FROM webhook_deliveries
        WHERE organisation_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (organisation_id, limit),
    )
    return [
        WebhookDeliveryResponse(
            id=str(r["id"]),
            webhook_id=str(r["webhook_id"]),
            invoice_id=r["invoice_id"],
            event_type=str(r["event_type"]),
            request_id=str(r["request_id"]),
            response_status=r["response_status"],
            response_body=r["response_body"],
            attempt_count=int(r["attempt_count"]),
            status=r["status"],
            delivered_at=r["delivered_at"],
            created_at=str(r["created_at"]),
        )
        for r in rows
    ]


async def dispatch_webhook_event(
    db: Database,
    settings: Settings,
    *,
    organisation_id: str,
    invoice_id: str,
    event_type: str,
    data: dict,
    http_client: httpx.AsyncClient | None = None,
) -> None:
    """Deliver signed webhook payloads for matching active subscriptions."""
    hooks = await db.fetchall(
        """
        SELECT id, url, signing_secret_enc, events_json
        FROM webhooks
        WHERE organisation_id = ? AND is_active = 1
        """,
        (organisation_id,),
    )
    if not hooks:
        return

    event_payload = {
        "id": generate_id("evt"),
        "type": event_type,
        "created_at": utc_now_iso(),
        "organisation_id": organisation_id,
        "data": data,
    }
    body_str = json.dumps(event_payload, separators=(",", ":"))
    body_bytes = body_str.encode("utf-8")

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=8.0)
    try:
        for hook in hooks:
            events = json.loads(str(hook["events_json"]))
            if event_type not in events:
                continue

            secret = decrypt_secret(str(hook["signing_secret_enc"]), settings.app_secret)
            timestamp = int(time.time())
            signature = sign_webhook_payload(secret, timestamp, body_bytes)
            delivery_id = generate_id("dlv")
            req_id = generate_id("whreq")

            status_label = "failed"
            resp_status: int | None = None
            resp_body: str | None = None
            delivered_at: str | None = None

            try:
                resp = await client.post(
                    str(hook["url"]),
                    content=body_bytes,
                    headers={
                        "Content-Type": "application/json",
                        "X-InvoiceLine-Event": event_type,
                        "X-InvoiceLine-Delivery": delivery_id,
                        "X-InvoiceLine-Signature": signature,
                    },
                )
                resp_status = resp.status_code
                resp_body = resp.text[:500]
                if 200 <= resp.status_code < 300:
                    status_label = "delivered"
                    delivered_at = utc_now_iso()
            except Exception as exc:
                resp_body = str(exc)[:500]

            await db.execute(
                """
                INSERT INTO webhook_deliveries (
                    id, webhook_id, organisation_id, invoice_id, event_type,
                    request_id, payload_json, response_status, response_body,
                    attempt_count, status, delivered_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    delivery_id,
                    hook["id"],
                    organisation_id,
                    invoice_id,
                    event_type,
                    req_id,
                    body_str,
                    resp_status,
                    resp_body,
                    status_label,
                    delivered_at,
                    utc_now_iso(),
                ),
            )
    finally:
        if owns_client:
            await client.aclose()
