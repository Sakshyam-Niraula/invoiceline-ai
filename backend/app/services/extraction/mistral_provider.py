from __future__ import annotations

import base64
import json

import httpx

from app.core.errors import ExternalProviderError
from app.core.files import estimate_page_count
from app.services.extraction.base import (
    EXTRACTION_PROMPT,
    ExtractedInvoiceData,
    normalize_extraction_payload,
)


class MistralExtractionProvider:
    """Invoice extraction via Mistral OCR + JSON structuring."""

    name = "mistral"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def extract(
        self,
        *,
        file_bytes: bytes,
        content_type: str,
        file_name: str,
    ) -> ExtractedInvoiceData:
        if not self._api_key:
            raise ExternalProviderError(
                "Mistral extraction provider is not configured (missing MISTRAL_API_KEY)."
            )

        encoded = base64.b64encode(file_bytes).decode("ascii")
        data_uri = f"data:{content_type};base64,{encoded}"
        headers = {"Authorization": f"Bearer {self._api_key}"}

        doc_payload = (
            {"type": "document_url", "document_url": data_uri}
            if content_type == "application/pdf"
            else {"type": "image_url", "image_url": data_uri}
        )

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                ocr_resp = await client.post(
                    "https://api.mistral.ai/v1/ocr",
                    headers=headers,
                    json={"model": "mistral-ocr-latest", "document": doc_payload},
                )
                if ocr_resp.status_code >= 400:
                    raise ExternalProviderError(
                        f"Mistral OCR returned HTTP {ocr_resp.status_code}",
                        details=ocr_resp.text[:500],
                    )
                ocr_json = ocr_resp.json()
                pages = ocr_json.get("pages") or []
                markdown_pages = "\n\n".join(
                    f"--- Page {p.get('index', idx) + 1} ---\n{p.get('markdown', '')}"
                    for idx, p in enumerate(pages)
                )

                chat_resp = await client.post(
                    "https://api.mistral.ai/v1/chat/completions",
                    headers=headers,
                    json={
                        "model": "mistral-small-latest",
                        "temperature": 0.0,
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {"role": "system", "content": EXTRACTION_PROMPT},
                            {
                                "role": "user",
                                "content": f"Filename: {file_name}\n\nOCR Output:\n{markdown_pages}",
                            },
                        ],
                    },
                )
        except httpx.HTTPError as exc:
            raise ExternalProviderError(f"Mistral API network failure: {exc}") from exc

        if chat_resp.status_code >= 400:
            raise ExternalProviderError(
                f"Mistral structuring returned HTTP {chat_resp.status_code}",
                details=chat_resp.text[:500],
            )

        try:
            content = chat_resp.json()["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ValueError("Root JSON is not an object")
        except Exception as exc:
            raise ExternalProviderError(
                f"Mistral returned invalid JSON payload: {exc}"
            ) from exc

        return normalize_extraction_payload(
            parsed,
            provider_name=self.name,
            fallback_page_count=max(
                len(pages), estimate_page_count(file_bytes, content_type)
            ),
        )
