from __future__ import annotations

from typing import Any

from workers import Response, WorkerEntrypoint, asgi

from app.config import load_worker_env
from app.db.d1_adapter import D1Database
from app.main import create_app
from app.queue.base import WorkerQueueProducer
from app.services.extraction.factory import create_extraction_provider
from app.services.invoice_service import process_invoice_job
from app.storage.supabase_adapter import SupabaseObjectStorage


def _build_worker_app(env: Any):
    settings = load_worker_env(env)
    db = D1Database(env.DB)
    storage = SupabaseObjectStorage(settings.supabase_url, settings.supabase_secret_key, settings.supabase_bucket)
    queue = WorkerQueueProducer(env.INVOICE_QUEUE)
    provider = create_extraction_provider(settings)
    return create_app(settings, db=db, storage=storage, queue=queue, extraction_provider=provider)


class Default(WorkerEntrypoint):
    """Cloudflare Python Worker entrypoint for HTTP ASGI and Queue consumers."""

    async def fetch(self, request: Any) -> Any:
        settings = load_worker_env(self.env)
        origin = request.headers.get("Origin")
        allowed = origin in settings.allowed_origins if origin else False

        # Answer browser preflight requests at the Worker edge. This avoids
        # relying on the ASGI adapter to preserve CORS middleware responses.
        if request.method == "OPTIONS" and request.headers.get("Access-Control-Request-Method"):
            if not allowed:
                return Response("Origin not allowed", status=403)
            requested_headers = request.headers.get("Access-Control-Request-Headers", "authorization,content-type")
            return Response(
                None,
                status=204,
                headers={
                    "Access-Control-Allow-Origin": origin,
                    "Access-Control-Allow-Credentials": "true",
                    "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
                    "Access-Control-Allow-Headers": requested_headers,
                    "Access-Control-Max-Age": "86400",
                    "Vary": "Origin",
                },
            )

        application = _build_worker_app(self.env)
        response = await asgi.fetch(application, request, self.env)

        # Add CORS headers at the Worker boundary to cover success and error
        # responses alike, including responses returned by the ASGI adapter.
        if allowed:
            # The ASGI adapter already returned a valid Worker Response.
            # Mutate its headers in place instead of reconstructing it: this
            # preserves the original status, cookies, and streaming body.
            response.headers.set("Access-Control-Allow-Origin", origin)
            response.headers.set("Access-Control-Allow-Credentials", "true")
            response.headers.set("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
            response.headers.set(
                "Access-Control-Allow-Headers",
                "Authorization, Content-Type, Idempotency-Key, X-Request-Id",
            )
            response.headers.append("Vary", "Origin")
        return response

    async def queue(self, batch: Any) -> None:
        settings = load_worker_env(self.env)
        db = D1Database(self.env.DB)
        storage = SupabaseObjectStorage(settings.supabase_url, settings.supabase_secret_key, settings.supabase_bucket)
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
