from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import AuthContext, get_db, get_settings, require_scope
from app.config import Settings
from app.db.base import Database
from app.schemas.webhooks import (
    CreateWebhookRequest,
    CreateWebhookResponse,
    WebhookDeliveryResponse,
    WebhookResponse,
)
from app.services import webhook_service

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])


@router.get("", response_model=list[WebhookResponse])
async def list_webhooks(
    auth: AuthContext = Depends(require_scope("webhooks:manage")),
    db: Database = Depends(get_db),
) -> list[WebhookResponse]:
    return await webhook_service.list_webhooks(db, auth.organisation_id)


@router.post("", response_model=CreateWebhookResponse, status_code=201)
async def create_webhook(
    payload: CreateWebhookRequest,
    auth: AuthContext = Depends(require_scope("webhooks:manage")),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CreateWebhookResponse:
    return await webhook_service.create_webhook(
        db,
        settings,
        auth.organisation_id,
        auth.user_id or auth.api_key_id,
        payload,
        request_id=auth.request_id,
    )


@router.delete("/{webhook_id}", status_code=204)
async def delete_webhook(
    webhook_id: str,
    auth: AuthContext = Depends(require_scope("webhooks:manage")),
    db: Database = Depends(get_db),
) -> None:
    await webhook_service.delete_webhook(
        db,
        auth.organisation_id,
        webhook_id,
        auth.user_id or auth.api_key_id,
        request_id=auth.request_id,
    )


@router.get("/deliveries", response_model=list[WebhookDeliveryResponse])
async def list_webhook_deliveries(
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(require_scope("webhooks:manage")),
    db: Database = Depends(get_db),
) -> list[WebhookDeliveryResponse]:
    return await webhook_service.list_webhook_deliveries(db, auth.organisation_id, limit=limit)
