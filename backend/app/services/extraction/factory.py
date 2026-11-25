from __future__ import annotations

from app.config import Settings
from app.core.errors import ExternalProviderError
from app.services.extraction.base import ExtractionProvider
from app.services.extraction.gemini_provider import GeminiExtractionProvider
from app.services.extraction.mistral_provider import MistralExtractionProvider


def create_extraction_provider(settings: Settings) -> ExtractionProvider:
    if settings.extraction_provider == "gemini":
        return GeminiExtractionProvider(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
        )
    if settings.extraction_provider == "mistral":
        return MistralExtractionProvider(api_key=settings.mistral_api_key)
    raise ExternalProviderError(
        f"Unsupported extraction provider: {settings.extraction_provider}"
    )
