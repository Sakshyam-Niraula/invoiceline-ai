from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

InvoiceStatus = Literal[
    "queued",
    "processing",
    "extracted",
    "needs_review",
    "approved",
    "rejected",
    "failed",
]

ValidationStatus = Literal[
    "pending",
    "passed",
    "warning",
    "failed",
    "requires_review",
]


class FieldConfidence(BaseModel):
    confidence: float | None = None
    source_reference: str | None = None


class LineItemSchema(BaseModel):
    id: str | None = None
    line_index: int = Field(ge=0)
    page_number: int | None = Field(default=1, ge=1)
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


class ValidationResultSchema(BaseModel):
    id: str
    rule_code: str
    severity: Literal["passed", "warning", "failed", "requires_review"]
    field_path: str | None = None
    line_index: int | None = None
    message: str
    expected_value: str | None = None
    actual_value: str | None = None
    created_at: str


class AuditEventSchema(BaseModel):
    id: str
    actor_type: str
    actor_id: str | None = None
    action: str
    metadata: dict[str, Any] | None = None
    created_at: str


class InvoiceSummarySchema(BaseModel):
    id: str
    file_name: str
    content_type: str
    file_size_bytes: int
    file_sha256: str
    page_count: int | None = None
    status: InvoiceStatus
    validation_status: ValidationStatus
    extraction_provider: str | None = None
    extraction_error: str | None = None
    supplier_name: str | None = None
    invoice_number: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    currency: str | None = None
    total_amount: str | None = None
    duplicate_file_detected: bool = False
    created_at: str
    updated_at: str


class InvoiceDetailSchema(InvoiceSummarySchema):
    supplier_address: str | None = None
    supplier_tax_id: str | None = None
    po_number: str | None = None
    subtotal: str | None = None
    tax_total: str | None = None
    discount_total: str | None = None
    payment_terms: str | None = None
    payment_details: dict[str, Any] | None = None
    field_metadata: dict[str, FieldConfidence] = Field(default_factory=dict)
    processing_started_at: str | None = None
    processing_completed_at: str | None = None
    reviewed_by_user_id: str | None = None
    reviewed_at: str | None = None
    review_notes: str | None = None
    document_url: str | None = None
    line_items: list[LineItemSchema] = Field(default_factory=list)
    validation_results: list[ValidationResultSchema] = Field(default_factory=list)
    history: list[AuditEventSchema] = Field(default_factory=list)


class InvoiceStatusSchema(BaseModel):
    id: str
    status: InvoiceStatus
    validation_status: ValidationStatus
    extraction_provider: str | None = None
    extraction_error: str | None = None
    processing_started_at: str | None = None
    processing_completed_at: str | None = None
    updated_at: str


class InvoiceListResponse(BaseModel):
    items: list[InvoiceSummarySchema]
    total: int
    limit: int
    offset: int


class InvoiceUploadResponse(BaseModel):
    invoices: list[InvoiceSummarySchema]


class ReviewLineItemInput(BaseModel):
    id: str | None = None
    page_number: int | None = Field(default=1, ge=1)
    description: str = ""
    sku: str | None = None
    quantity: str | None = None
    unit: str | None = None
    unit_price: str | None = None
    discount: str | None = None
    tax_rate: str | None = None
    tax_amount: str | None = None
    line_total: str | None = None
    source_reference: str | None = None


class InvoiceReviewRequest(BaseModel):
    action: Literal["save", "approve", "reject"] = "save"
    review_notes: str | None = Field(default=None, max_length=2000)
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
    line_items: list[ReviewLineItemInput] | None = None
