from __future__ import annotations

from typing import Protocol


class ObjectStorage(Protocol):
    """Private binary object storage abstraction (Cloudflare R2 / local dev)."""

    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def get(self, key: str) -> bytes | None: ...

    async def delete(self, key: str) -> None: ...
