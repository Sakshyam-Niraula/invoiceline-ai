from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ExtractedFieldMeta:
    confidence: float | None = None
    source_reference: str | None = None


@dataclass(frozen=True)
class ExtractedLineItem:
    line_index: int
    page_number: int | None = 1
    description: str = ""
    sku: str | None = None
    quantity: str | None = None
    unit: str | None = None
    unit_price: str | None = None
    discount: str | None = None
    tax_rate: str | None = None
    tax_amount: str | None = None
    line_total: str | None = None
    confidence: float | None = None
    source_reference: str | None = None


@dataclass(frozen=True)
class ExtractedInvoiceData:
    provider_name: str
    page_count: int
    supplier_name: str | None = None
    supplier_address: str | None = None
    supplier_tax_id: str | None = None
    invoice_number: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    currency: str | None = None
    po_number: str | None = None
    subtotal: str | None = None
    tax_total: str | None = None
    discount_total: str | None = None
    total_amount: str | None = None
    payment_terms: str | None = None
    payment_details: dict[str, Any] | None = None
    field_metadata: dict[str, ExtractedFieldMeta] = field(default_factory=dict)
    line_items: list[ExtractedLineItem] = field(default_factory=list)
    raw_response: dict[str, Any] = field(default_factory=dict)


class ExtractionProvider(Protocol):
    name: str

    async def extract(
        self,
        *,
        file_bytes: bytes,
        content_type: str,
        file_name: str,
    ) -> ExtractedInvoiceData: ...


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _opt_num_str(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value).strip().replace(",", "")
    return s if s else None


def _opt_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, num))


def normalize_extraction_payload(
    raw: dict[str, Any],
    *,
    provider_name: str,
    fallback_page_count: int = 1,
) -> ExtractedInvoiceData:
    """Normalize provider JSON payload into strict ExtractedInvoiceData."""
    raw_meta = raw.get("field_metadata") or {}
    field_metadata: dict[str, ExtractedFieldMeta] = {}
    if isinstance(raw_meta, dict):
        for k, v in raw_meta.items():
            if isinstance(v, dict):
                field_metadata[str(k)] = ExtractedFieldMeta(
                    confidence=_opt_float(v.get("confidence")),
                    source_reference=_opt_str(v.get("source_reference")),
                )

    raw_items = raw.get("line_items") or []
    line_items: list[ExtractedLineItem] = []
    if isinstance(raw_items, list):
        for idx, item in enumerate(raw_items):
            if not isinstance(item, dict):
                continue
            page_num = item.get("page_number")
            try:
                resolved_page = max(1, int(page_num)) if page_num is not None else 1
            except (TypeError, ValueError):
                resolved_page = 1
            line_items.append(
                ExtractedLineItem(
                    line_index=idx,
                    page_number=resolved_page,
                    description=str(item.get("description") or "").strip(),
                    sku=_opt_str(item.get("sku")),
                    quantity=_opt_num_str(item.get("quantity")),
                    unit=_opt_str(item.get("unit")),
                    unit_price=_opt_num_str(item.get("unit_price")),
                    discount=_opt_num_str(item.get("discount")),
                    tax_rate=_opt_num_str(item.get("tax_rate")),
                    tax_amount=_opt_num_str(item.get("tax_amount")),
                    line_total=_opt_num_str(item.get("line_total")),
                    confidence=_opt_float(item.get("confidence")),
                    source_reference=_opt_str(item.get("source_reference")),
                )
            )

    raw_pages = raw.get("page_count")
    try:
        page_count = max(1, int(raw_pages)) if raw_pages is not None else fallback_page_count
    except (TypeError, ValueError):
        page_count = fallback_page_count

    currency = _opt_str(raw.get("currency"))
    if currency:
        currency = currency.upper()

    payment_details = raw.get("payment_details")
    if not isinstance(payment_details, dict) or not payment_details:
        payment_details = None

    return ExtractedInvoiceData(
        provider_name=provider_name,
        page_count=page_count,
        supplier_name=_opt_str(raw.get("supplier_name")),
        supplier_address=_opt_str(raw.get("supplier_address")),
        supplier_tax_id=_opt_str(raw.get("supplier_tax_id")),
        invoice_number=_opt_str(raw.get("invoice_number")),
        invoice_date=_opt_str(raw.get("invoice_date")),
        due_date=_opt_str(raw.get("due_date")),
        currency=currency,
        po_number=_opt_str(raw.get("po_number")),
        subtotal=_opt_num_str(raw.get("subtotal")),
        tax_total=_opt_num_str(raw.get("tax_total")),
        discount_total=_opt_num_str(raw.get("discount_total")),
        total_amount=_opt_num_str(raw.get("total_amount")),
        payment_terms=_opt_str(raw.get("payment_terms")),
        payment_details=payment_details,
        field_metadata=field_metadata,
        line_items=line_items,
        raw_response=raw,
    )


EXTRACTION_PROMPT = """You are a financial document extraction engine.
Extract all invoice header fields and every line item across ALL pages of this document.
If a table continues across multiple pages, extract every row in reading order with its exact page_number.
Rules:
- Dates MUST be formatted as ISO YYYY-MM-DD when determinable.
- Currency MUST be a 3-letter ISO-4217 code (e.g., USD, EUR, GBP) if determinable.
- Monetary values and quantities MUST be plain decimal strings without currency symbols or thousands separators (e.g., "1250.00", "2.5").
- For every extracted header field in field_metadata, provide a confidence score between 0.0 and 1.0 and a concise source_reference (e.g., "Page 1, top right").
- For every line item, provide confidence (0.0 to 1.0) and source_reference (e.g., "Page 2, row 3").
- Never invent values not present in the document; return null for missing fields."""

INVOICE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "page_count": {"type": "integer"},
        "supplier_name": {"type": ["string", "null"]},
        "supplier_address": {"type": ["string", "null"]},
        "supplier_tax_id": {"type": ["string", "null"]},
        "invoice_number": {"type": ["string", "null"]},
        "invoice_date": {"type": ["string", "null"]},
        "due_date": {"type": ["string", "null"]},
        "currency": {"type": ["string", "null"]},
        "po_number": {"type": ["string", "null"]},
        "subtotal": {"type": ["string", "null"]},
        "tax_total": {"type": ["string", "null"]},
        "discount_total": {"type": ["string", "null"]},
        "total_amount": {"type": ["string", "null"]},
        "payment_terms": {"type": ["string", "null"]},
        "payment_details": {
            "type": ["object", "null"],
            "properties": {
                "bank_name": {"type": ["string", "null"]},
                "iban": {"type": ["string", "null"]},
                "bic_swift": {"type": ["string", "null"]},
                "account_number": {"type": ["string", "null"]},
                "routing_number": {"type": ["string", "null"]},
                "payment_reference": {"type": ["string", "null"]},
            },
        },
        "field_metadata": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "confidence": {"type": ["number", "null"]},
                    "source_reference": {"type": ["string", "null"]},
                },
            },
        },
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "page_number": {"type": ["integer", "null"]},
                    "description": {"type": "string"},
                    "sku": {"type": ["string", "null"]},
                    "quantity": {"type": ["string", "null"]},
                    "unit": {"type": ["string", "null"]},
                    "unit_price": {"type": ["string", "null"]},
                    "discount": {"type": ["string", "null"]},
                    "tax_rate": {"type": ["string", "null"]},
                    "tax_amount": {"type": ["string", "null"]},
                    "line_total": {"type": ["string", "null"]},
                    "confidence": {"type": ["number", "null"]},
                    "source_reference": {"type": ["string", "null"]},
                },
                "required": ["description"],
            },
        },
    },
    "required": ["page_count", "line_items"],
}
