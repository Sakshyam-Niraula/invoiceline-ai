from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base structured HTTP application error."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers or {}


class BadRequestError(AppError):
    def __init__(self, message: str, code: str = "bad_request", details: Any = None) -> None:
        super().__init__(400, code, message, details)


class UnauthorizedError(AppError):
    def __init__(self, message: str = "Authentication required", code: str = "unauthorized") -> None:
        super().__init__(401, code, message)


class ForbiddenError(AppError):
    def __init__(self, message: str = "Insufficient permissions", code: str = "forbidden") -> None:
        super().__init__(403, code, message)


class NotFoundError(AppError):
    def __init__(self, message: str = "Resource not found", code: str = "not_found") -> None:
        super().__init__(404, code, message)


class ConflictError(AppError):
    def __init__(self, message: str, code: str = "conflict", details: Any = None) -> None:
        super().__init__(409, code, message, details)


class PayloadTooLargeError(AppError):
    def __init__(self, message: str = "File exceeds maximum allowed size") -> None:
        super().__init__(413, "payload_too_large", message)


class UnsupportedMediaTypeError(AppError):
    def __init__(self, message: str = "Unsupported file type") -> None:
        super().__init__(415, "unsupported_media_type", message)


class RateLimitError(AppError):
    def __init__(self, retry_after: int) -> None:
        super().__init__(
            429,
            "rate_limit_exceeded",
            "Rate limit exceeded. Retry later.",
            details={"retry_after_seconds": retry_after},
            headers={"Retry-After": str(retry_after)},
        )


class ExternalProviderError(AppError):
    def __init__(self, message: str, details: Any = None) -> None:
        super().__init__(502, "extraction_provider_error", message, details)
