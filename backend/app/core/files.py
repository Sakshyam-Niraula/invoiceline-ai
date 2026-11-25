from __future__ import annotations

import re
from pathlib import Path

from app.core.errors import BadRequestError, PayloadTooLargeError, UnsupportedMediaTypeError

ALLOWED_MIME_TYPES: dict[str, str] = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}

EXT_TO_MIME: dict[str, str] = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}

_PDF_PAGE_PATTERN = re.compile(rb"/Type\s*/Page(?![a-zA-Z])")


def sanitize_filename(raw_name: str | None) -> str:
    name = Path(raw_name or "invoice.pdf").name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return cleaned[:120] or "invoice.pdf"


def validate_upload_bytes(
    *,
    data: bytes,
    filename: str | None,
    declared_content_type: str | None,
    max_bytes: int,
) -> tuple[str, str]:
    """Validate upload size, extension, and magic bytes. Returns (safe_name, content_type)."""
    if not data:
        raise BadRequestError("Uploaded file is empty", code="empty_file")
    if len(data) > max_bytes:
        raise PayloadTooLargeError(
            f"File exceeds maximum allowed size of {max_bytes // (1024 * 1024)} MB"
        )

    safe_name = sanitize_filename(filename)
    ext = Path(safe_name).suffix.lower()
    inferred_mime = EXT_TO_MIME.get(ext)
    declared = (declared_content_type or "").split(";", 1)[0].strip().lower()
    if declared == "image/jpg":
        declared = "image/jpeg"

    detected = _detect_magic_mime(data)
    if detected is None:
        raise UnsupportedMediaTypeError(
            "Unsupported file signature. Only valid PDF, JPEG, and PNG documents are accepted."
        )

    if declared and declared != "application/octet-stream" and declared != detected:
        raise UnsupportedMediaTypeError(
            f"File content ({detected}) does not match declared Content-Type ({declared})."
        )
    if inferred_mime and inferred_mime != detected:
        raise UnsupportedMediaTypeError(
            f"File content ({detected}) does not match file extension ({ext})."
        )

    return safe_name, detected


def _detect_magic_mime(data: bytes) -> str | None:
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return None


def estimate_page_count(data: bytes, content_type: str) -> int:
    if content_type != "application/pdf":
        return 1
    matches = _PDF_PAGE_PATTERN.findall(data)
    return max(1, len(matches))
