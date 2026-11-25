from __future__ import annotations

from fastapi import APIRouter, Depends, File, Header, Query, Request, Response, UploadFile

from app.api.deps import (
    AuthContext,
    get_auth_context,
    get_db,
    get_queue,
    get_settings,
    get_storage,
    require_scope,
)
from app.config import Settings
from app.core.crypto import verify_download_token
from app.core.errors import BadRequestError, NotFoundError, UnauthorizedError
from app.db.base import Database
from app.queue.base import QueueProducer
from app.schemas.invoices import (
    InvoiceDetailSchema,
    InvoiceListResponse,
    InvoiceReviewRequest,
    InvoiceStatusSchema,
    InvoiceUploadResponse,
)
from app.services import invoice_service
from app.storage.base import ObjectStorage

router = APIRouter(prefix="/v1/invoices", tags=["invoices"])


@router.post("", response_model=InvoiceUploadResponse, status_code=201)
async def upload_invoices(
    request: Request,
    files: list[UploadFile] | None = File(default=None),
    file: UploadFile | None = File(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    auth: AuthContext = Depends(require_scope("invoices:write")),
    db: Database = Depends(get_db),
    storage: ObjectStorage = Depends(get_storage),
    queue: QueueProducer = Depends(get_queue),
    settings: Settings = Depends(get_settings),
) -> InvoiceUploadResponse:
    uploads: list[UploadFile] = []
    if files:
        uploads.extend(files)
    if file is not None:
        uploads.append(file)
    if not uploads:
        raise BadRequestError("Provide at least one file under 'file' or 'files'", code="no_files")

    file_tuples: list[tuple[str | None, str | None, bytes]] = []
    total_pages = 0
    for up in uploads:
        raw = await up.read()
        file_tuples.append((up.filename, up.content_type, raw))

    created = await invoice_service.upload_invoices(
        db,
        storage,
        queue,
        settings,
        organisation_id=auth.organisation_id,
        actor_type=auth.actor_type,
        actor_id=auth.user_id or auth.api_key_id,
        files=file_tuples,
        idempotency_key=idempotency_key,
        request_id=auth.request_id,
    )
    for item in created:
        total_pages += item.page_count or 1
    request.state.pages_processed = total_pages
    return InvoiceUploadResponse(invoices=created)


@router.get("", response_model=InvoiceListResponse)
async def list_invoices(
    q: str | None = Query(default=None, max_length=120),
    status: str | None = Query(default=None),
    validation_status: str | None = Query(default=None),
    sort_by: str = Query(default="created_at"),
    sort_dir: str = Query(default="desc"),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(require_scope("invoices:read")),
    db: Database = Depends(get_db),
) -> InvoiceListResponse:
    return await invoice_service.list_invoices(
        db,
        organisation_id=auth.organisation_id,
        q=q,
        status=status,
        validation_status=validation_status,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset,
    )


@router.get("/{invoice_id}", response_model=InvoiceDetailSchema)
async def get_invoice(
    invoice_id: str,
    auth: AuthContext = Depends(require_scope("invoices:read")),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> InvoiceDetailSchema:
    return await invoice_service.get_invoice_detail(
        db,
        settings,
        organisation_id=auth.organisation_id,
        invoice_id=invoice_id,
    )


@router.get("/{invoice_id}/status", response_model=InvoiceStatusSchema)
async def get_invoice_status(
    invoice_id: str,
    auth: AuthContext = Depends(require_scope("invoices:read")),
    db: Database = Depends(get_db),
) -> InvoiceStatusSchema:
    return await invoice_service.get_invoice_status(
        db,
        organisation_id=auth.organisation_id,
        invoice_id=invoice_id,
    )


@router.get("/{invoice_id}/results", response_model=InvoiceDetailSchema)
async def get_invoice_results(
    invoice_id: str,
    auth: AuthContext = Depends(require_scope("invoices:read")),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> InvoiceDetailSchema:
    return await invoice_service.get_invoice_detail(
        db,
        settings,
        organisation_id=auth.organisation_id,
        invoice_id=invoice_id,
    )


@router.post("/{invoice_id}/validate", response_model=InvoiceDetailSchema)
async def validate_invoice(
    invoice_id: str,
    auth: AuthContext = Depends(require_scope("invoices:write")),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> InvoiceDetailSchema:
    return await invoice_service.revalidate_invoice(
        db,
        settings,
        organisation_id=auth.organisation_id,
        invoice_id=invoice_id,
        actor_type=auth.actor_type,
        actor_id=auth.user_id or auth.api_key_id,
        request_id=auth.request_id,
    )


@router.post("/{invoice_id}/review", response_model=InvoiceDetailSchema)
async def review_invoice(
    invoice_id: str,
    payload: InvoiceReviewRequest,
    auth: AuthContext = Depends(require_scope("invoices:review")),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> InvoiceDetailSchema:
    return await invoice_service.review_invoice(
        db,
        settings,
        organisation_id=auth.organisation_id,
        invoice_id=invoice_id,
        actor_type=auth.actor_type,
        actor_id=auth.user_id or auth.api_key_id,
        payload=payload,
        request_id=auth.request_id,
    )


@router.get("/{invoice_id}/export")
async def export_invoice(
    invoice_id: str,
    format: str = Query(default="json", pattern="^(json|csv)$"),
    auth: AuthContext = Depends(require_scope("invoices:read")),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    detail = await invoice_service.get_invoice_detail(
        db,
        settings,
        organisation_id=auth.organisation_id,
        invoice_id=invoice_id,
    )
    if format == "csv":
        csv_body = invoice_service.format_invoice_csv(detail)
        return Response(
            content=csv_body,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{invoice_id}.csv"',
            },
        )
    return Response(
        content=detail.model_dump_json(indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{invoice_id}.json"',
        },
    )


@router.get("/{invoice_id}/document")
async def get_invoice_document(
    invoice_id: str,
    request: Request,
    token: str | None = Query(default=None),
    db: Database = Depends(get_db),
    storage: ObjectStorage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> Response:
    if token:
        try:
            tok_inv_id, org_id = verify_download_token(token, settings.app_secret)
        except ValueError as exc:
            raise UnauthorizedError(str(exc), code="invalid_download_token") from exc
        if tok_inv_id != invoice_id:
            raise UnauthorizedError("Download token mismatch", code="invalid_download_token")
    else:
        auth = await get_auth_context(request, db, settings)
        if not auth.has_scope("invoices:read"):
            from app.core.errors import ForbiddenError

            raise ForbiddenError("Missing invoices:read scope")
        org_id = auth.organisation_id

    row = await db.fetchone(
        """
        SELECT file_name, content_type, r2_key
        FROM invoices
        WHERE id = ? AND organisation_id = ? AND deleted_at IS NULL
        """,
        (invoice_id, org_id),
    )
    if row is None:
        raise NotFoundError("Invoice not found")

    data = await storage.get(str(row["r2_key"]))
    if data is None:
        raise NotFoundError("Stored document file not found")

    return Response(
        content=data,
        media_type=str(row["content_type"]),
        headers={
            "Content-Disposition": f'inline; filename="{row["file_name"]}"',
            "Cache-Control": "private, max-age=60",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/{invoice_id}", status_code=204)
async def delete_invoice(
    invoice_id: str,
    auth: AuthContext = Depends(require_scope("invoices:write")),
    db: Database = Depends(get_db),
    storage: ObjectStorage = Depends(get_storage),
) -> None:
    if auth.actor_type == "user" and not auth.has_scope("invoices:delete"):
        from app.core.errors import ForbiddenError

        raise ForbiddenError("Deleting invoices requires owner or admin permissions")
    await invoice_service.delete_invoice(
        db,
        storage,
        organisation_id=auth.organisation_id,
        invoice_id=invoice_id,
        actor_type=auth.actor_type,
        actor_id=auth.user_id or auth.api_key_id,
        request_id=auth.request_id,
    )
