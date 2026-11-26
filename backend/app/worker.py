from __future__ import annotations

import json
from typing import Any

from workers import Response, WorkerEntrypoint, asgi

from app.config import load_worker_env
from app.core.crypto import utc_now_iso
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


def _normalise_queue_body(body: Any) -> Any:
    """Convert Cloudflare/Pyodide queue payloads into native Python values."""
    to_py = getattr(body, "to_py", None)
    if callable(to_py):
        body = to_py()

    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            return body

    if isinstance(body, dict):
        return body

    # Be tolerant of JS objects exposed through the Python FFI.
    items = getattr(body, "items", None)
    if callable(items):
        try:
            return dict(items())
        except Exception:
            pass

    return body


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
        """Process each message independently and persist failures on its invoice."""
        print("[invoice-queue] batch received")

        for message in batch.messages:
            invoice_id = ""
            organisation_id = ""
            db = None
            try:
                body = _normalise_queue_body(message.body)
                if not isinstance(body, dict):
                    print(f"[invoice-queue] unsupported message body type: {type(body).__name__}")
                    message.ack()
                    continue

                if body.get("type") != "extract_invoice":
                    print(f"[invoice-queue] ignored unknown message type: {body.get('type')!r}")
                    message.ack()
                    continue

                invoice_id = str(body.get("invoice_id") or "").strip()
                organisation_id = str(body.get("organisation_id") or "").strip()
                if not invoice_id or not organisation_id:
                    print("[invoice-queue] invalid extraction message: missing invoice_id or organisation_id")
                    message.ack()
                    continue

                # Create the database adapter first and mark the job before loading
                # optional services. Previously, setup exceptions happened before
                # the invoice row was touched, leaving the UI stuck at "queued".
                db = D1Database(self.env.DB)
                started_at = utc_now_iso()
                await db.execute(
                    """
                    UPDATE invoices
                    SET status = 'processing', processing_started_at = ?,
                        extraction_error = NULL, updated_at = ?
                    WHERE id = ? AND organisation_id = ? AND deleted_at IS NULL
                    """,
                    (started_at, started_at, invoice_id, organisation_id),
                )

                print(f"[invoice-queue] setting up extraction for invoice {invoice_id}")
                settings = load_worker_env(self.env)
                storage = SupabaseObjectStorage(
                    settings.supabase_url,
                    settings.supabase_secret_key,
                    settings.supabase_bucket,
                )
                provider = create_extraction_provider(settings)

                await process_invoice_job(
                    db,
                    storage,
                    settings,
                    provider,
                    invoice_id=invoice_id,
                    organisation_id=organisation_id,
                )
                message.ack()
                print(f"[invoice-queue] completed invoice job {invoice_id}")

            except Exception as exc:
                error = f"Queue consumer error: {type(exc).__name__}: {exc}"[:1000]
                print(f"[invoice-queue] invoice job failed: {error}")
                if db is not None and invoice_id and organisation_id:
                    try:
                        completed_at = utc_now_iso()
                        await db.execute(
                            """
                            UPDATE invoices
                            SET status = 'failed', extraction_error = ?,
                                processing_completed_at = ?, updated_at = ?
                            WHERE id = ? AND organisation_id = ?
                              AND status IN ('queued', 'processing')
                            """,
                            (error, completed_at, completed_at, invoice_id, organisation_id),
                        )
                        # The failure is now visible on the invoice; don't send
                        # the same permanently failing setup job through the DLQ.
                        message.ack()
                        continue
                    except Exception as persist_exc:
                        print(
                            "[invoice-queue] could not persist failure for "
                            f"{invoice_id}: {type(persist_exc).__name__}: {persist_exc}"
                        )
                message.retry()
