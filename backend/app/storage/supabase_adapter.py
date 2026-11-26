from __future__ import annotations

from urllib.parse import quote

import httpx


class SupabaseObjectStorage:
    """Private Supabase Storage adapter for invoice PDFs."""

    def __init__(self, project_url: str, secret_key: str, bucket: str = "invoiceline-documents") -> None:
        self._base = project_url.rstrip("/")
        self._secret = secret_key
        self._bucket = bucket

    def _url(self, key: str) -> str:
        encoded = quote(key.lstrip("/"), safe="/")
        return f"{self._base}/storage/v1/object/{self._bucket}/{encoded}"

    def _headers(self, content_type: str = "application/octet-stream") -> dict[str, str]:
        # New Supabase secret API keys (sb_secret_...) are opaque API keys,
        # not JWTs. They belong in the apikey header only. Sending one as a
        # Bearer token makes Storage reject it with "Invalid Compact JWS".
        headers = {
            "apikey": self._secret,
            "Content-Type": content_type,
            "x-upsert": "true",
        }
        # Keep compatibility with legacy service_role JWT keys.
        if not self._secret.startswith("sb_secret_"):
            headers["Authorization"] = f"Bearer {self._secret}"
        return headers

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(self._url(key), content=data, headers=self._headers(content_type))
            response.raise_for_status()

    async def get(self, key: str) -> bytes | None:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(self._url(key), headers=self._headers())
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.content

    async def delete(self, key: str) -> None:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.delete(self._url(key), headers=self._headers())
            if response.status_code not in (200, 204, 404):
                response.raise_for_status()
