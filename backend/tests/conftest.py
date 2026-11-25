from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.db.migrations import apply_migrations
from app.db.sqlite_adapter import SQLiteDatabase
from app.main import create_app
from app.queue.local_queue import LocalAsyncQueueProducer
from app.services.extraction.base import ExtractedInvoiceData
from app.services.invoice_service import process_invoice_job
from app.storage.local_adapter import LocalObjectStorage


class StubExtractionProvider:
    """Deterministic test extraction provider for backend unit/integration tests."""

    name = "test-stub"

    def __init__(self) -> None:
        self.next_result: ExtractedInvoiceData | None = None
        self.next_error: Exception | None = None

    async def extract(
        self,
        *,
        file_bytes: bytes,
        content_type: str,
        file_name: str,
    ) -> ExtractedInvoiceData:
        if self.next_error is not None:
            raise self.next_error
        assert self.next_result is not None
        return self.next_result


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    return Settings.from_mapping(
        {
            "ENVIRONMENT": "test",
            "APP_SECRET": "test-secret-key-32-bytes-minimum-1234",
            "DATABASE_PATH": str(tmp_path / "test.db"),
            "LOCAL_STORAGE_PATH": str(tmp_path / "storage"),
            "EXTRACTION_PROVIDER": "gemini",
            "GEMINI_API_KEY": "",
            "ALLOWED_ORIGINS": "http://localhost:3000",
            "RATE_LIMIT_PER_MINUTE": "120",
            "PUBLIC_BASE_URL": "http://testserver",
        }
    )


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[SQLiteDatabase]:
    database = SQLiteDatabase(str(tmp_path / "test.db"))
    await apply_migrations(database)
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def storage(tmp_path: Path) -> LocalObjectStorage:
    return LocalObjectStorage(str(tmp_path / "storage"))


@pytest.fixture
def stub_provider() -> StubExtractionProvider:
    return StubExtractionProvider()


@pytest.fixture
async def client(
    test_settings: Settings,
    db: SQLiteDatabase,
    storage: LocalObjectStorage,
    stub_provider: StubExtractionProvider,
) -> AsyncIterator[httpx.AsyncClient]:
    async def _handler(invoice_id: str, organisation_id: str) -> None:
        await process_invoice_job(
            db,
            storage,
            test_settings,
            stub_provider,
            invoice_id=invoice_id,
            organisation_id=organisation_id,
        )

    queue = LocalAsyncQueueProducer(_handler, eager=True)
    app = create_app(
        test_settings,
        db=db,
        storage=storage,
        queue=queue,
        extraction_provider=stub_provider,
        eager_queue=True,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client
