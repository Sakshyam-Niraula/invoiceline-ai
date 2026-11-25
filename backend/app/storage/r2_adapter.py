from __future__ import annotations

from typing import Any


class R2ObjectStorage:
    """Cloudflare R2 bucket adapter for Python Workers."""

    def __init__(self, bucket: Any) -> None:
        self._bucket = bucket

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await self._bucket.put(
            key,
            data,
            httpMetadata={"contentType": content_type},
        )

    async def get(self, key: str) -> bytes | None:
        obj = await self._bucket.get(key)
        if obj is None:
            return None
        buf = await obj.arrayBuffer()
        if hasattr(buf, "to_bytes"):
            return buf.to_bytes()
        to_py = getattr(buf, "to_py", None)
        if callable(to_py):
            return bytes(to_py())
        return bytes(buf)

    async def delete(self, key: str) -> None:
        await self._bucket.delete(key)
