from __future__ import annotations

from typing import Any

from workers import WorkerEntrypoint, asgi

from app.config import load_worker_env
from app.db.d1_adapter import D1Database
from app.main import create_app
from app.queue.base import WorkerQueueProducer
from app.services.extraction.factory import create_extraction_provider
from app.services.invoice_service import process_invoice_job
from app.storage.r2_adapter import R2ObjectStorage


def _build_worker_app(env: Any):
    settings = load_worker_env(env)
    db = D1Database(env.DB)
    storage = R2ObjectStorage(env.BUCKET)
    queue = WorkerQueueProducer(env.INVOICE_QUEUE)
    provider = create_extraction_provider(settings)
    return create_app(
        settings,
        db=db,
        storage=storage,
        queue=queue,
        extraction_provider=provider,
    )


class Default(WorkerEntrypoint):
    """Cloudflare Python Worker entrypoint for HTTP ASGI and Queue consumers."""

    async def fetch(self, request: Any) -> Any:
        application = _build_worker_app(self.env)
        return await asgi.fetch(application, request, self.env)

    async def queue(self, batch: Any) -> None:
        settings = load_worker_env(self.env)
        db = D1Database(self.env.DB)
        storage = R2ObjectStorage(self.env.BUCKET)
        provider = create_extraction_provider(settings)

        for message in batch.messages:
            body = message.body
            to_py = getattr(body, "to_py", None)
            if callable(to_py):
                body = to_py()
            if isinstance(body, dict) and body.get("type") == "extract_invoice":
                await process_invoice_job(
                    db,
                    storage,
                    settings,
                    provider,
                    invoice_id=str(body["invoice_id"]),
                    organisation_id=str(body["organisation_id"]),
                )
            message.ack()
