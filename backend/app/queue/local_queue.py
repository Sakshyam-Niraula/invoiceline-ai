from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger("invoiceline.queue")

JobHandler = Callable[[str, str], Awaitable[None]]


class LocalAsyncQueueProducer:
    """In-process asyncio task queue for local development and tests."""

    def __init__(self, handler: JobHandler, *, eager: bool = False) -> None:
        self._handler = handler
        self._eager = eager
        self._tasks: set[asyncio.Task[None]] = set()

    async def enqueue_invoice(self, invoice_id: str, organisation_id: str) -> None:
        if self._eager:
            await self._run(invoice_id, organisation_id)
            return

        task = asyncio.create_task(self._run(invoice_id, organisation_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, invoice_id: str, organisation_id: str) -> None:
        try:
            await self._handler(invoice_id, organisation_id)
        except Exception:
            logger.exception("Background invoice job failed for %s", invoice_id)

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
