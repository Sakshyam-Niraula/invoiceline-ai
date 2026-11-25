from __future__ import annotations

import base64
import json

import httpx

from app.core.errors import ExternalProviderError
from app.core.files import estimate_page_count
from app.services.extraction.base import (
    EXTRACTION_PROMPT,
    INVOICE_JSON_SCHEMA,
    ExtractedInvoiceData,
    normalize_extraction_payload,
)


class GeminiExtractionProvider:
    """Multimodal invoice extraction via Google Gemini REST API."""

    name = "gemini"

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        self._api_key = api_key
        self._model = model

    async def extract(
        self,
        *,
        file_bytes: bytes,
        content_type: str,
        file_name: str,
    ) -> ExtractedInvoiceData:
        if not self._api_key:
            raise ExternalProviderError(
                "Gemini extraction provider is not configured (missing GEMINI_API_KEY)."
            )

        encoded = base64.b64encode(file_bytes).decode("ascii")
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self._model}:generateContent"
        )
        body = {
            "contents": [
                {
                    "parts": [
                        {"text": f"{EXTRACTION_PROMPT}\nFilename: {file_name}"},
                        {"inline_data": {"mime_type": content_type, "data": encoded}},
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.0,
                "responseMimeType": "application/json",
                "responseJsonSchema": INVOICE_JSON_SCHEMA,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    url,
                    params={"key": self._api_key},
                    json=body,
                )
        except httpx.HTTPError as exc:
            raise ExternalProviderError(f"Gemini API network failure: {exc}") from exc

        if resp.status_code >= 400:
            raise ExternalProviderError(
                f"Gemini API returned HTTP {resp.status_code}",
                details=resp.text[:500],
            )

        payload = resp.json()
        try:
            text_out = payload["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text_out)
            if not isinstance(parsed, dict):
                raise ValueError("Root JSON is not an object")
        except Exception as exc:
            raise ExternalProviderError(
                f"Gemini returned invalid structured payload: {exc}"
            ) from exc

        return normalize_extraction_payload(
            parsed,
            provider_name=self.name,
            fallback_page_count=estimate_page_count(file_bytes, content_type),
        )
