from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

WebhookEventType = Literal[
    "invoice.extracted",
    "invoice.failed",
    "invoice.approved",
    "invoice.rejected",
]


class CreateWebhookRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=500)
    events: list[WebhookEventType] = Field(
        default_factory=lambda: [
            "invoice.extracted",
            "invoice.failed",
            "invoice.approved",
            "invoice.rejected",
        ]
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        cleaned = v.strip()
        if not (cleaned.startswith("https://") or cleaned.startswith("http://")):
            raise ValueError("Webhook URL must start with https:// or http://")
        return cleaned


class WebhookResponse(BaseModel):
    id: str
    url: str
    secret_prefix: str
    events: list[str]
    is_active: bool
    created_at: str
    updated_at: str


class CreateWebhookResponse(WebhookResponse):
    signing_secret: str


class WebhookDeliveryResponse(BaseModel):
    id: str
    webhook_id: str
    invoice_id: str | None = None
    event_type: str
    request_id: str
    response_status: int | None = None
    response_body: str | None = None
    attempt_count: int
    status: Literal["pending", "delivered", "failed"]
    delivered_at: str | None = None
    created_at: str
