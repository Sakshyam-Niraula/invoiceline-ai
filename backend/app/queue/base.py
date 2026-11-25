from __future__ import annotations

from typing import Any, Protocol


class QueueProducer(Protocol):
    """Queue producer abstraction for Cloudflare Queues and local async background tasks."""

    async def enqueue_invoice(self, invoice_id: str, organisation_id: str) -> None: ...


class WorkerQueueProducer:
    """Cloudflare Queues producer binding wrapper."""

    def __init__(self, binding: Any) -> None:
        self._queue = binding

    async def enqueue_invoice(self, invoice_id: str, organisation_id: str) -> None:
        await self._queue.send(
            {
                "type": "extract_invoice",
                "invoice_id": invoice_id,
                "organisation_id": organisation_id,
            }
        )
