from __future__ import annotations

import csv
import io
import json
from typing import Any

import httpx

from app.config import Settings
from app.core.crypto import generate_id, sha256_bytes, sign_download_token, utc_now_iso
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.core.files import estimate_page_count, validate_upload_bytes
from app.db.base import Database, Statement
from app.queue.base import QueueProducer
from app.schemas.invoices import (
    AuditEventSchema,
    FieldConfidence,
    InvoiceDetailSchema,
    InvoiceListResponse,
    InvoiceReviewRequest,
    InvoiceStatusSchema,
    InvoiceSummarySchema,
    LineItemSchema,
    ValidationResultSchema,
)
from app.services.audit_service import build_audit_statement, record_audit
from app.services.extraction.base import ExtractionProvider
from app.services.validation_engine import validate_invoice_data
from app.services.webhook_service import dispatch_webhook_event
from app.storage.base import ObjectStorage

SORT_COLUMNS = {
    "created_at": "created_at",
    "invoice_date": "invoice_date",
    "total_amount": "CAST(COALESCE(total_amount, '0') AS REAL)",
    "supplier_name": "LOWER(COALESCE(supplier_name, ''))",
    "status": "status",
}


def _row_to_summary(row: dict[str, Any], *, duplicate_file: bool = False) -> InvoiceSummarySchema:
    return InvoiceSummarySchema(
        id=str(row["id"]),
        file_name=str(row["file_name"]),
        content_type=str(row["content_type"]),
        file_size_bytes=int(row["file_size_bytes"]),
        file_sha256=str(row["file_sha256"]),
        page_count=row["page_count"],
        status=row["status"],
        validation_status=row["validation_status"],
        extraction_provider=row["extraction_provider"],
        extraction_error=row["extraction_error"],
        supplier_name=row["supplier_name"],
        invoice_number=row["invoice_number"],
        invoice_date=row["invoice_date"],
        due_date=row["due_date"],
        currency=row["currency"],
        total_amount=row["total_amount"],
        duplicate_file_detected=duplicate_file,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


async def upload_invoices(
    db: Database,
    storage: ObjectStorage,
    queue: QueueProducer,
    settings: Settings,
    *,
    organisation_id: str,
    actor_type: str,
    actor_id: str | None,
    files: list[tuple[str | None, str | None, bytes]],
    idempotency_key: str | None = None,
    request_id: str | None = None,
) -> list[InvoiceSummarySchema]:
    if not files:
        raise BadRequestError("At least one invoice file must be provided", code="no_files")

    clean_idem = idempotency_key.strip() if idempotency_key else None
    if clean_idem:
        if len(files) > 1:
            raise BadRequestError(
                "Idempotency-Key can only be used with single-file uploads",
                code="idempotency_single_file_only",
            )
        existing = await db.fetchone(
            """
            SELECT * FROM invoices
            WHERE organisation_id = ? AND idempotency_key = ? AND deleted_at IS NULL
            """,
            (organisation_id, clean_idem),
        )
        if existing is not None:
            return [_row_to_summary(existing)]

    created: list[InvoiceSummarySchema] = []
    for raw_name, declared_ct, data in files:
        safe_name, content_type = validate_upload_bytes(
            data=data,
            filename=raw_name,
            declared_content_type=declared_ct,
            max_bytes=settings.max_upload_bytes,
        )
        file_hash = sha256_bytes(data)
        page_count = estimate_page_count(data, content_type)
        dup_row = await db.fetchone(
            """
            SELECT id FROM invoices
            WHERE organisation_id = ? AND file_sha256 = ? AND deleted_at IS NULL
            LIMIT 1
            """,
            (organisation_id, file_hash),
        )
        duplicate_file = dup_row is not None

        invoice_id = generate_id("inv")
        r2_key = f"orgs/{organisation_id}/invoices/{invoice_id}/{file_hash[:12]}_{safe_name}"
        await storage.put(r2_key, data, content_type)

        now = utc_now_iso()
        try:
            await db.batch(
                [
                    Statement(
                        sql="""
                        INSERT INTO invoices (
                            id, organisation_id, uploaded_by_user_id, idempotency_key,
                            file_name, content_type, file_size_bytes, file_sha256, r2_key,
                            page_count, status, validation_status, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 'pending', ?, ?)
                        """,
                        params=(
                            invoice_id,
                            organisation_id,
                            actor_id if actor_type == "user" else None,
                            clean_idem,
                            safe_name,
                            content_type,
                            len(data),
                            file_hash,
                            r2_key,
                            page_count,
                            now,
                            now,
                        ),
                    ),
                    build_audit_statement(
                        organisation_id=organisation_id,
                        actor_type=actor_type,
                        actor_id=actor_id,
                        action="invoice.uploaded",
                        resource_type="invoice",
                        resource_id=invoice_id,
                        metadata={
                            "file_name": safe_name,
                            "content_type": content_type,
                            "file_size_bytes": len(data),
                            "duplicate_file_detected": duplicate_file,
                            "duplicate_of_invoice_id": dup_row["id"] if dup_row else None,
                        },
                        request_id=request_id,
                    ),
                ]
            )
        except Exception as exc:
            await storage.delete(r2_key)
            if clean_idem and "UNIQUE" in str(exc).upper():
                raise ConflictError("Duplicate idempotency key in concurrent request") from exc
            raise

        await queue.enqueue_invoice(invoice_id, organisation_id)

        row = await db.fetchone(
            "SELECT * FROM invoices WHERE id = ? AND organisation_id = ?",
            (invoice_id, organisation_id),
        )
        assert row is not None
        created.append(_row_to_summary(row, duplicate_file=duplicate_file))

    return created


async def process_invoice_job(
    db: Database,
    storage: ObjectStorage,
    settings: Settings,
    provider: ExtractionProvider,
    *,
    invoice_id: str,
    organisation_id: str,
    http_client: httpx.AsyncClient | None = None,
) -> None:
    """Execute extraction and deterministic validation for a queued invoice."""
    row = await db.fetchone(
        """
        SELECT * FROM invoices
        WHERE id = ? AND organisation_id = ? AND deleted_at IS NULL
        """,
        (invoice_id, organisation_id),
    )
    if row is None:
        return

    started_at = utc_now_iso()
    await db.execute(
        """
        UPDATE invoices
        SET status = 'processing', processing_started_at = ?, extraction_error = NULL, updated_at = ?
        WHERE id = ? AND organisation_id = ?
        """,
        (started_at, started_at, invoice_id, organisation_id),
    )

    try:
        file_bytes = await storage.get(str(row["r2_key"]))
        if file_bytes is None:
            raise FileNotFoundError(f"Stored invoice document missing: {row['r2_key']}")

        extracted = await provider.extract(
            file_bytes=file_bytes,
            content_type=str(row["content_type"]),
            file_name=str(row["file_name"]),
        )
    except Exception as exc:
        completed_at = utc_now_iso()
        err_msg = str(exc)[:1000]
        await db.batch(
            [
                Statement(
                    sql="""
                    UPDATE invoices
                    SET status = 'failed', extraction_provider = ?, extraction_error = ?,
                        processing_completed_at = ?, updated_at = ?
                    WHERE id = ? AND organisation_id = ?
                    """,
                    params=(
                        provider.name,
                        err_msg,
                        completed_at,
                        completed_at,
                        invoice_id,
                        organisation_id,
                    ),
                ),
                build_audit_statement(
                    organisation_id=organisation_id,
                    actor_type="system",
                    actor_id=None,
                    action="invoice.failed",
                    resource_type="invoice",
                    resource_id=invoice_id,
                    metadata={"provider": provider.name, "error": err_msg},
                ),
            ]
        )
        await dispatch_webhook_event(
            db,
            settings,
            organisation_id=organisation_id,
            invoice_id=invoice_id,
            event_type="invoice.failed",
            data={"invoice_id": invoice_id, "status": "failed", "error": err_msg},
            http_client=http_client,
        )
        return

    header_dict = {
        "supplier_name": extracted.supplier_name,
        "supplier_address": extracted.supplier_address,
        "supplier_tax_id": extracted.supplier_tax_id,
        "invoice_number": extracted.invoice_number,
        "invoice_date": extracted.invoice_date,
        "due_date": extracted.due_date,
        "currency": extracted.currency,
        "po_number": extracted.po_number,
        "subtotal": extracted.subtotal,
        "tax_total": extracted.tax_total,
        "discount_total": extracted.discount_total,
        "total_amount": extracted.total_amount,
    }
    meta_dict = {
        k: {"confidence": v.confidence, "source_reference": v.source_reference}
        for k, v in extracted.field_metadata.items()
    }
    line_dicts = [
        {
            "line_index": item.line_index,
            "page_number": item.page_number,
            "description": item.description,
            "sku": item.sku,
            "quantity": item.quantity,
            "unit": item.unit,
            "unit_price": item.unit_price,
            "discount": item.discount,
            "tax_rate": item.tax_rate,
            "tax_amount": item.tax_amount,
            "line_total": item.line_total,
            "confidence": item.confidence,
            "source_reference": item.source_reference,
        }
        for item in extracted.line_items
    ]

    val_status, findings = await validate_invoice_data(
        db,
        organisation_id=organisation_id,
        invoice_id=invoice_id,
        header=header_dict,
        line_items=line_dicts,
        field_metadata=meta_dict,
    )

    next_status = "extracted" if val_status == "passed" else "needs_review"
    completed_at = utc_now_iso()

    statements: list[Statement] = [
        Statement(
            sql="""
            UPDATE invoices
            SET page_count = ?, status = ?, validation_status = ?, extraction_provider = ?,
                extraction_error = NULL, processing_completed_at = ?,
                supplier_name = ?, supplier_address = ?, supplier_tax_id = ?,
                invoice_number = ?, invoice_date = ?, due_date = ?, currency = ?, po_number = ?,
                subtotal = ?, tax_total = ?, discount_total = ?, total_amount = ?,
                payment_terms = ?, payment_details_json = ?, field_metadata_json = ?,
                raw_extraction_json = ?, updated_at = ?
            WHERE id = ? AND organisation_id = ?
            """,
            params=(
                extracted.page_count,
                next_status,
                val_status,
                extracted.provider_name,
                completed_at,
                extracted.supplier_name,
                extracted.supplier_address,
                extracted.supplier_tax_id,
                extracted.invoice_number,
                extracted.invoice_date,
                extracted.due_date,
                extracted.currency,
                extracted.po_number,
                extracted.subtotal,
                extracted.tax_total,
                extracted.discount_total,
                extracted.total_amount,
                extracted.payment_terms,
                json.dumps(extracted.payment_details) if extracted.payment_details else None,
                json.dumps(meta_dict),
                json.dumps(extracted.raw_response),
                completed_at,
                invoice_id,
                organisation_id,
            ),
        ),
        Statement(
            sql="DELETE FROM invoice_line_items WHERE invoice_id = ? AND organisation_id = ?",
            params=(invoice_id, organisation_id),
        ),
        Statement(
            sql="DELETE FROM validation_results WHERE invoice_id = ? AND organisation_id = ?",
            params=(invoice_id, organisation_id),
        ),
    ]

    for item in line_dicts:
        statements.append(
            Statement(
                sql="""
                INSERT INTO invoice_line_items (
                    id, invoice_id, organisation_id, line_index, page_number,
                    description, sku, quantity, unit, unit_price, discount,
                    tax_rate, tax_amount, line_total, confidence, source_reference,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                params=(
                    generate_id("li"),
                    invoice_id,
                    organisation_id,
                    item["line_index"],
                    item["page_number"],
                    item["description"],
                    item["sku"],
                    item["quantity"],
                    item["unit"],
                    item["unit_price"],
                    item["discount"],
                    item["tax_rate"],
                    item["tax_amount"],
                    item["line_total"],
                    item["confidence"],
                    item["source_reference"],
                    completed_at,
                    completed_at,
                ),
            )
        )

    for finding in findings:
        statements.append(
            Statement(
                sql="""
                INSERT INTO validation_results (
                    id, invoice_id, organisation_id, rule_code, severity,
                    field_path, line_index, message, expected_value, actual_value, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                params=(
                    generate_id("val"),
                    invoice_id,
                    organisation_id,
                    finding.rule_code,
                    finding.severity,
                    finding.field_path,
                    finding.line_index,
                    finding.message,
                    finding.expected_value,
                    finding.actual_value,
                    completed_at,
                ),
            )
        )

    statements.append(
        build_audit_statement(
            organisation_id=organisation_id,
            actor_type="system",
            actor_id=None,
            action="invoice.extracted",
            resource_type="invoice",
            resource_id=invoice_id,
            metadata={
                "provider": extracted.provider_name,
                "page_count": extracted.page_count,
                "line_item_count": len(line_dicts),
                "validation_status": val_status,
                "status": next_status,
            },
        )
    )
    await db.batch(statements)

    await dispatch_webhook_event(
        db,
        settings,
        organisation_id=organisation_id,
        invoice_id=invoice_id,
        event_type="invoice.extracted",
        data={
            "invoice_id": invoice_id,
            "status": next_status,
            "validation_status": val_status,
            "invoice_number": extracted.invoice_number,
            "total_amount": extracted.total_amount,
            "currency": extracted.currency,
        },
        http_client=http_client,
    )


async def list_invoices(
    db: Database,
    *,
    organisation_id: str,
    q: str | None = None,
    status: str | None = None,
    validation_status: str | None = None,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    limit: int = 25,
    offset: int = 0,
) -> InvoiceListResponse:
    clauses = ["organisation_id = ?", "deleted_at IS NULL"]
    params: list[Any] = [organisation_id]

    if status:
        clauses.append("status = ?")
        params.append(status)
    if validation_status:
        clauses.append("validation_status = ?")
        params.append(validation_status)
    if q and q.strip():
        like = f"%{q.strip()}%"
        clauses.append(
            "(file_name LIKE ? OR COALESCE(supplier_name, '') LIKE ? "
            "OR COALESCE(invoice_number, '') LIKE ? OR COALESCE(po_number, '') LIKE ?)"
        )
        params.extend([like, like, like, like])

    where_sql = " AND ".join(clauses)
    order_col = SORT_COLUMNS.get(sort_by, "created_at")
    direction = "ASC" if sort_dir.lower() == "asc" else "DESC"

    count_row = await db.fetchone(
        f"SELECT COUNT(*) AS cnt FROM invoices WHERE {where_sql}",
        tuple(params),
    )
    total = int(count_row["cnt"]) if count_row else 0

    rows = await db.fetchall(
        f"""
        SELECT * FROM invoices
        WHERE {where_sql}
        ORDER BY {order_col} {direction}, created_at DESC
        LIMIT ? OFFSET ?
        """,
        tuple([*params, limit, offset]),
    )
    return InvoiceListResponse(
        items=[_row_to_summary(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


async def get_invoice_detail(
    db: Database,
    settings: Settings,
    *,
    organisation_id: str,
    invoice_id: str,
) -> InvoiceDetailSchema:
    row = await db.fetchone(
        """
        SELECT * FROM invoices
        WHERE id = ? AND organisation_id = ? AND deleted_at IS NULL
        """,
        (invoice_id, organisation_id),
    )
    if row is None:
        raise NotFoundError("Invoice not found")

    line_rows = await db.fetchall(
        """
        SELECT * FROM invoice_line_items
        WHERE invoice_id = ? AND organisation_id = ?
        ORDER BY line_index ASC
        """,
        (invoice_id, organisation_id),
    )
    val_rows = await db.fetchall(
        """
        SELECT * FROM validation_results
        WHERE invoice_id = ? AND organisation_id = ?
        ORDER BY created_at ASC
        """,
        (invoice_id, organisation_id),
    )
    audit_rows = await db.fetchall(
        """
        SELECT id, actor_type, actor_id, action, metadata_json, created_at
        FROM audit_logs
        WHERE organisation_id = ? AND resource_type = 'invoice' AND resource_id = ?
        ORDER BY created_at ASC
        """,
        (organisation_id, invoice_id),
    )

    dup_row = await db.fetchone(
        """
        SELECT id FROM invoices
        WHERE organisation_id = ? AND file_sha256 = ? AND id != ? AND deleted_at IS NULL
        LIMIT 1
        """,
        (organisation_id, row["file_sha256"], invoice_id),
    )

    token, _ = sign_download_token(
        invoice_id,
        organisation_id,
        settings.signed_url_ttl_seconds,
        settings.app_secret,
    )
    doc_url = f"{settings.public_base_url}/v1/invoices/{invoice_id}/document?token={token}"

    raw_meta = json.loads(str(row["field_metadata_json"])) if row["field_metadata_json"] else {}
    field_metadata = {
        k: FieldConfidence(
            confidence=v.get("confidence"),
            source_reference=v.get("source_reference"),
        )
        for k, v in raw_meta.items()
        if isinstance(v, dict)
    }

    return InvoiceDetailSchema(
        id=str(row["id"]),
        file_name=str(row["file_name"]),
        content_type=str(row["content_type"]),
        file_size_bytes=int(row["file_size_bytes"]),
        file_sha256=str(row["file_sha256"]),
        page_count=row["page_count"],
        status=row["status"],
        validation_status=row["validation_status"],
        extraction_provider=row["extraction_provider"],
        extraction_error=row["extraction_error"],
        supplier_name=row["supplier_name"],
        supplier_address=row["supplier_address"],
        supplier_tax_id=row["supplier_tax_id"],
        invoice_number=row["invoice_number"],
        invoice_date=row["invoice_date"],
        due_date=row["due_date"],
        currency=row["currency"],
        po_number=row["po_number"],
        subtotal=row["subtotal"],
        tax_total=row["tax_total"],
        discount_total=row["discount_total"],
        total_amount=row["total_amount"],
        payment_terms=row["payment_terms"],
        payment_details=(
            json.loads(str(row["payment_details_json"])) if row["payment_details_json"] else None
        ),
        field_metadata=field_metadata,
        processing_started_at=row["processing_started_at"],
        processing_completed_at=row["processing_completed_at"],
        reviewed_by_user_id=row["reviewed_by_user_id"],
        reviewed_at=row["reviewed_at"],
        review_notes=row["review_notes"],
        duplicate_file_detected=dup_row is not None,
        document_url=doc_url,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        line_items=[
            LineItemSchema(
                id=str(li["id"]),
                line_index=int(li["line_index"]),
                page_number=li["page_number"],
                description=str(li["description"]),
                sku=li["sku"],
                quantity=li["quantity"],
                unit=li["unit"],
                unit_price=li["unit_price"],
                discount=li["discount"],
                tax_rate=li["tax_rate"],
                tax_amount=li["tax_amount"],
                line_total=li["line_total"],
                confidence=li["confidence"],
                source_reference=li["source_reference"],
            )
            for li in line_rows
        ],
        validation_results=[
            ValidationResultSchema(
                id=str(v["id"]),
                rule_code=str(v["rule_code"]),
                severity=v["severity"],
                field_path=v["field_path"],
                line_index=v["line_index"],
                message=str(v["message"]),
                expected_value=v["expected_value"],
                actual_value=v["actual_value"],
                created_at=str(v["created_at"]),
            )
            for v in val_rows
        ],
        history=[
            AuditEventSchema(
                id=str(a["id"]),
                actor_type=str(a["actor_type"]),
                actor_id=a["actor_id"],
                action=str(a["action"]),
                metadata=json.loads(str(a["metadata_json"])) if a["metadata_json"] else None,
                created_at=str(a["created_at"]),
            )
            for a in audit_rows
        ],
    )


async def get_invoice_status(
    db: Database,
    *,
    organisation_id: str,
    invoice_id: str,
) -> InvoiceStatusSchema:
    row = await db.fetchone(
        """
        SELECT id, status, validation_status, extraction_provider, extraction_error,
               processing_started_at, processing_completed_at, updated_at
        FROM invoices
        WHERE id = ? AND organisation_id = ? AND deleted_at IS NULL
        """,
        (invoice_id, organisation_id),
    )
    if row is None:
        raise NotFoundError("Invoice not found")
    return InvoiceStatusSchema(
        id=str(row["id"]),
        status=row["status"],
        validation_status=row["validation_status"],
        extraction_provider=row["extraction_provider"],
        extraction_error=row["extraction_error"],
        processing_started_at=row["processing_started_at"],
        processing_completed_at=row["processing_completed_at"],
        updated_at=str(row["updated_at"]),
    )


async def revalidate_invoice(
    db: Database,
    settings: Settings,
    *,
    organisation_id: str,
    invoice_id: str,
    actor_type: str,
    actor_id: str | None,
    request_id: str | None = None,
) -> InvoiceDetailSchema:
    detail = await get_invoice_detail(db, settings, organisation_id=organisation_id, invoice_id=invoice_id)
    header = {
        "supplier_name": detail.supplier_name,
        "invoice_number": detail.invoice_number,
        "invoice_date": detail.invoice_date,
        "due_date": detail.due_date,
        "currency": detail.currency,
        "subtotal": detail.subtotal,
        "tax_total": detail.tax_total,
        "discount_total": detail.discount_total,
        "total_amount": detail.total_amount,
    }
    meta = {
        k: {"confidence": v.confidence, "source_reference": v.source_reference}
        for k, v in detail.field_metadata.items()
    }
    lines = [li.model_dump() for li in detail.line_items]

    val_status, findings = await validate_invoice_data(
        db,
        organisation_id=organisation_id,
        invoice_id=invoice_id,
        header=header,
        line_items=lines,
        field_metadata=meta,
    )

    now = utc_now_iso()
    next_status = detail.status
    if detail.status in ("extracted", "needs_review"):
        next_status = "extracted" if val_status == "passed" else "needs_review"

    statements: list[Statement] = [
        Statement(
            sql="""
            UPDATE invoices
            SET validation_status = ?, status = ?, updated_at = ?
            WHERE id = ? AND organisation_id = ?
            """,
            params=(val_status, next_status, now, invoice_id, organisation_id),
        ),
        Statement(
            sql="DELETE FROM validation_results WHERE invoice_id = ? AND organisation_id = ?",
            params=(invoice_id, organisation_id),
        ),
    ]
    for finding in findings:
        statements.append(
            Statement(
                sql="""
                INSERT INTO validation_results (
                    id, invoice_id, organisation_id, rule_code, severity,
                    field_path, line_index, message, expected_value, actual_value, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                params=(
                    generate_id("val"),
                    invoice_id,
                    organisation_id,
                    finding.rule_code,
                    finding.severity,
                    finding.field_path,
                    finding.line_index,
                    finding.message,
                    finding.expected_value,
                    finding.actual_value,
                    now,
                ),
            )
        )
    statements.append(
        build_audit_statement(
            organisation_id=organisation_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action="invoice.validated",
            resource_type="invoice",
            resource_id=invoice_id,
            metadata={"validation_status": val_status},
            request_id=request_id,
        )
    )
    await db.batch(statements)
    return await get_invoice_detail(db, settings, organisation_id=organisation_id, invoice_id=invoice_id)


async def review_invoice(
    db: Database,
    settings: Settings,
    *,
    organisation_id: str,
    invoice_id: str,
    actor_type: str,
    actor_id: str | None,
    payload: InvoiceReviewRequest,
    request_id: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> InvoiceDetailSchema:
    row = await db.fetchone(
        "SELECT * FROM invoices WHERE id = ? AND organisation_id = ? AND deleted_at IS NULL",
        (invoice_id, organisation_id),
    )
    if row is None:
        raise NotFoundError("Invoice not found")

    if row["status"] in ("queued", "processing"):
        raise BadRequestError("Cannot review an invoice while extraction is in progress")

    data_patch = payload.model_dump(exclude_unset=True)
    header_fields = (
        "supplier_name",
        "supplier_address",
        "supplier_tax_id",
        "invoice_number",
        "invoice_date",
        "due_date",
        "currency",
        "po_number",
        "subtotal",
        "tax_total",
        "discount_total",
        "total_amount",
        "payment_terms",
    )
    updated_header: dict[str, Any] = {f: row[f] for f in header_fields}
    for f in header_fields:
        if f in data_patch:
            val = data_patch[f]
            if f == "currency" and isinstance(val, str):
                val = val.strip().upper() or None
            updated_header[f] = val

    payment_details_json = row["payment_details_json"]
    if "payment_details" in data_patch:
        payment_details_json = (
            json.dumps(payload.payment_details) if payload.payment_details is not None else None
        )

    existing_lines = await db.fetchall(
        """
        SELECT * FROM invoice_line_items
        WHERE invoice_id = ? AND organisation_id = ?
        ORDER BY line_index ASC
        """,
        (invoice_id, organisation_id),
    )

    if payload.line_items is not None:
        # Human-edited line items are treated as verified confidence 1.0
        final_lines = [
            {
                "id": li.id or generate_id("li"),
                "line_index": idx,
                "page_number": li.page_number or 1,
                "description": li.description.strip(),
                "sku": li.sku,
                "quantity": li.quantity,
                "unit": li.unit,
                "unit_price": li.unit_price,
                "discount": li.discount,
                "tax_rate": li.tax_rate,
                "tax_amount": li.tax_amount,
                "line_total": li.line_total,
                "confidence": 1.0,
                "source_reference": li.source_reference or "Human verified",
            }
            for idx, li in enumerate(payload.line_items)
        ]
    else:
        final_lines = existing_lines

    # When fields are explicitly corrected during review, refresh confidence for corrected fields
    meta_dict = json.loads(str(row["field_metadata_json"])) if row["field_metadata_json"] else {}
    for f in header_fields:
        if f in data_patch:
            prev_src = (
                meta_dict.get(f, {}).get("source_reference")
                if isinstance(meta_dict.get(f), dict)
                else None
            )
            meta_dict[f] = {"confidence": 1.0, "source_reference": prev_src or "Human verified"}

    val_status, findings = await validate_invoice_data(
        db,
        organisation_id=organisation_id,
        invoice_id=invoice_id,
        header=updated_header,
        line_items=final_lines,
        field_metadata=meta_dict,
    )

    if payload.action == "approve" and val_status == "failed":
        raise BadRequestError(
            "Cannot approve an invoice with failed validation checks. Correct errors first.",
            code="validation_failed",
        )

    now = utc_now_iso()
    if payload.action == "approve":
        next_status = "approved"
    elif payload.action == "reject":
        next_status = "rejected"
    else:
        next_status = "extracted" if val_status == "passed" else "needs_review"

    reviewed_by = actor_id if payload.action in ("approve", "reject") else row["reviewed_by_user_id"]
    reviewed_at = now if payload.action in ("approve", "reject") else row["reviewed_at"]
    review_notes = payload.review_notes if payload.review_notes is not None else row["review_notes"]

    statements: list[Statement] = [
        Statement(
            sql="""
            UPDATE invoices
            SET status = ?, validation_status = ?,
                supplier_name = ?, supplier_address = ?, supplier_tax_id = ?,
                invoice_number = ?, invoice_date = ?, due_date = ?, currency = ?, po_number = ?,
                subtotal = ?, tax_total = ?, discount_total = ?, total_amount = ?,
                payment_terms = ?, payment_details_json = ?, field_metadata_json = ?,
                reviewed_by_user_id = ?, reviewed_at = ?, review_notes = ?, updated_at = ?
            WHERE id = ? AND organisation_id = ?
            """,
            params=(
                next_status,
                val_status,
                updated_header["supplier_name"],
                updated_header["supplier_address"],
                updated_header["supplier_tax_id"],
                updated_header["invoice_number"],
                updated_header["invoice_date"],
                updated_header["due_date"],
                updated_header["currency"],
                updated_header["po_number"],
                updated_header["subtotal"],
                updated_header["tax_total"],
                updated_header["discount_total"],
                updated_header["total_amount"],
                updated_header["payment_terms"],
                payment_details_json,
                json.dumps(meta_dict),
                reviewed_by,
                reviewed_at,
                review_notes,
                now,
                invoice_id,
                organisation_id,
            ),
        ),
        Statement(
            sql="DELETE FROM validation_results WHERE invoice_id = ? AND organisation_id = ?",
            params=(invoice_id, organisation_id),
        ),
    ]

    if payload.line_items is not None:
        statements.append(
            Statement(
                sql="DELETE FROM invoice_line_items WHERE invoice_id = ? AND organisation_id = ?",
                params=(invoice_id, organisation_id),
            )
        )
        for li in final_lines:
            statements.append(
                Statement(
                    sql="""
                    INSERT INTO invoice_line_items (
                        id, invoice_id, organisation_id, line_index, page_number,
                        description, sku, quantity, unit, unit_price, discount,
                        tax_rate, tax_amount, line_total, confidence, source_reference,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    params=(
                        li["id"],
                        invoice_id,
                        organisation_id,
                        li["line_index"],
                        li["page_number"],
                        li["description"],
                        li["sku"],
                        li["quantity"],
                        li["unit"],
                        li["unit_price"],
                        li["discount"],
                        li["tax_rate"],
                        li["tax_amount"],
                        li["line_total"],
                        li["confidence"],
                        li["source_reference"],
                        now,
                        now,
                    ),
                )
            )

    for finding in findings:
        statements.append(
            Statement(
                sql="""
                INSERT INTO validation_results (
                    id, invoice_id, organisation_id, rule_code, severity,
                    field_path, line_index, message, expected_value, actual_value, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                params=(
                    generate_id("val"),
                    invoice_id,
                    organisation_id,
                    finding.rule_code,
                    finding.severity,
                    finding.field_path,
                    finding.line_index,
                    finding.message,
                    finding.expected_value,
                    finding.actual_value,
                    now,
                ),
            )
        )

    audit_action = f"invoice.{payload.action}" if payload.action in ("approve", "reject") else "invoice.corrected"
    statements.append(
        build_audit_statement(
            organisation_id=organisation_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action=audit_action,
            resource_type="invoice",
            resource_id=invoice_id,
            metadata={"validation_status": val_status, "status": next_status},
            request_id=request_id,
        )
    )
    await db.batch(statements)

    if payload.action in ("approve", "reject"):
        await dispatch_webhook_event(
            db,
            settings,
            organisation_id=organisation_id,
            invoice_id=invoice_id,
            event_type=f"invoice.{payload.action}d" if payload.action == "approve" else "invoice.rejected",
            data={
                "invoice_id": invoice_id,
                "status": next_status,
                "validation_status": val_status,
                "invoice_number": updated_header["invoice_number"],
                "total_amount": updated_header["total_amount"],
                "currency": updated_header["currency"],
            },
            http_client=http_client,
        )

    return await get_invoice_detail(db, settings, organisation_id=organisation_id, invoice_id=invoice_id)


async def delete_invoice(
    db: Database,
    storage: ObjectStorage,
    *,
    organisation_id: str,
    invoice_id: str,
    actor_type: str,
    actor_id: str | None,
    request_id: str | None = None,
) -> None:
    row = await db.fetchone(
        "SELECT id, r2_key FROM invoices WHERE id = ? AND organisation_id = ? AND deleted_at IS NULL",
        (invoice_id, organisation_id),
    )
    if row is None:
        raise NotFoundError("Invoice not found")

    now = utc_now_iso()
    await db.execute(
        "UPDATE invoices SET deleted_at = ?, updated_at = ? WHERE id = ? AND organisation_id = ?",
        (now, now, invoice_id, organisation_id),
    )
    await storage.delete(str(row["r2_key"]))
    await record_audit(
        db,
        organisation_id=organisation_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action="invoice.deleted",
        resource_type="invoice",
        resource_id=invoice_id,
        request_id=request_id,
    )


def format_invoice_csv(detail: InvoiceDetailSchema) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "invoice_id",
            "supplier_name",
            "supplier_tax_id",
            "invoice_number",
            "invoice_date",
            "due_date",
            "currency",
            "subtotal",
            "tax_total",
            "discount_total",
            "total_amount",
            "status",
            "validation_status",
            "line_index",
            "page_number",
            "description",
            "sku",
            "quantity",
            "unit",
            "unit_price",
            "discount",
            "tax_rate",
            "tax_amount",
            "line_total",
        ]
    )
    if not detail.line_items:
        writer.writerow(
            [
                detail.id,
                detail.supplier_name or "",
                detail.supplier_tax_id or "",
                detail.invoice_number or "",
                detail.invoice_date or "",
                detail.due_date or "",
                detail.currency or "",
                detail.subtotal or "",
                detail.tax_total or "",
                detail.discount_total or "",
                detail.total_amount or "",
                detail.status,
                detail.validation_status,
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            ]
        )
    else:
        for li in detail.line_items:
            writer.writerow(
                [
                    detail.id,
                    detail.supplier_name or "",
                    detail.supplier_tax_id or "",
                    detail.invoice_number or "",
                    detail.invoice_date or "",
                    detail.due_date or "",
                    detail.currency or "",
                    detail.subtotal or "",
                    detail.tax_total or "",
                    detail.discount_total or "",
                    detail.total_amount or "",
                    detail.status,
                    detail.validation_status,
                    li.line_index,
                    li.page_number or "",
                    li.description,
                    li.sku or "",
                    li.quantity or "",
                    li.unit or "",
                    li.unit_price or "",
                    li.discount or "",
                    li.tax_rate or "",
                    li.tax_amount or "",
                    li.line_total or "",
                ]
            )
    return buf.getvalue()
