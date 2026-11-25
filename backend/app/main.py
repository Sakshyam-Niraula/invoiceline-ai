from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import api_keys, auth, invoices, orgs, usage, webhooks
from app.config import Settings, get_local_settings
from app.core.crypto import generate_id, utc_now_iso
from app.core.errors import AppError
from app.db.base import Database
from app.db.migrations import apply_migrations
from app.db.sqlite_adapter import SQLiteDatabase
from app.queue.base import QueueProducer
from app.queue.local_queue import LocalAsyncQueueProducer
from app.services.extraction.base import ExtractionProvider
from app.services.extraction.factory import create_extraction_provider
from app.services.invoice_service import process_invoice_job
from app.storage.base import ObjectStorage
from app.storage.local_adapter import LocalObjectStorage

logger = logging.getLogger("invoiceline")


def create_app(
    settings: Settings | None = None,
    *,
    db: Database | None = None,
    storage: ObjectStorage | None = None,
    queue: QueueProducer | None = None,
    extraction_provider: ExtractionProvider | None = None,
    eager_queue: bool = False,
) -> FastAPI:
    resolved_settings = settings or get_local_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.settings = resolved_settings
        managed_sqlite: SQLiteDatabase | None = None

        if db is not None:
            application.state.db = db
        elif not hasattr(application.state, "db"):
            managed_sqlite = SQLiteDatabase(resolved_settings.database_path)
            await apply_migrations(managed_sqlite)
            application.state.db = managed_sqlite

        if storage is not None:
            application.state.storage = storage
        elif not hasattr(application.state, "storage"):
            application.state.storage = LocalObjectStorage(resolved_settings.local_storage_path)

        provider = extraction_provider or getattr(application.state, "extraction_provider", None)
        if provider is None:
            provider = create_extraction_provider(resolved_settings)
        application.state.extraction_provider = provider

        if queue is not None:
            application.state.queue = queue
        elif not hasattr(application.state, "queue"):

            async def _handle_job(invoice_id: str, organisation_id: str) -> None:
                await process_invoice_job(
                    application.state.db,
                    application.state.storage,
                    application.state.settings,
                    application.state.extraction_provider,
                    invoice_id=invoice_id,
                    organisation_id=organisation_id,
                )

            application.state.queue = LocalAsyncQueueProducer(_handle_job, eager=eager_queue)

        yield

        local_q = getattr(application.state, "queue", None)
        if isinstance(local_q, LocalAsyncQueueProducer):
            await local_q.drain()
        if managed_sqlite is not None:
            managed_sqlite.close()

    app = FastAPI(
        title="InvoiceLine AI API",
        version="1.0.0",
        docs_url="/v1/docs",
        openapi_url="/v1/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    if db is not None:
        app.state.db = db
    if storage is not None:
        app.state.storage = storage
    if queue is not None:
        app.state.queue = queue
    if extraction_provider is not None:
        app.state.extraction_provider = extraction_provider

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-Id"],
        expose_headers=["X-Request-Id", "Retry-After"],
    )

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        req_id = request.headers.get("x-request-id") or generate_id("req")
        request.state.request_id = req_id
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-Id"] = req_id

        auth_ctx = getattr(request.state, "auth", None)
        db_conn = getattr(request.app.state, "db", None)
        if (
            auth_ctx is not None
            and auth_ctx.actor_type == "api_key"
            and db_conn is not None
            and request.url.path.startswith("/v1/")
        ):
            latency_ms = int((time.perf_counter() - started) * 1000)
            pages = int(getattr(request.state, "pages_processed", 0))
            try:
                await db_conn.execute(
                    """
                    INSERT INTO api_request_logs (
                        id, organisation_id, api_key_id, request_id, method, path,
                        status_code, pages_processed, latency_ms, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        generate_id("log"),
                        auth_ctx.organisation_id,
                        auth_ctx.api_key_id,
                        req_id,
                        request.method,
                        request.url.path,
                        response.status_code,
                        pages,
                        latency_ms,
                        utc_now_iso(),
                    ),
                )
            except Exception:
                logger.warning("Failed to record API request log", exc_info=True)
        return response

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        req_id = getattr(request.state, "request_id", None)
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "request_id": req_id,
                    "details": exc.details,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        req_id = getattr(request.state, "request_id", None)
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed",
                    "request_id": req_id,
                    "details": exc.errors(),
                }
            },
        )

    @app.get("/health", tags=["health"])
    @app.get("/v1/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "invoiceline-api"}

    app.include_router(auth.router)
    app.include_router(orgs.router)
    app.include_router(api_keys.router)
    app.include_router(invoices.router)
    app.include_router(webhooks.router)
    app.include_router(usage.router)

    return app


app = create_app()
