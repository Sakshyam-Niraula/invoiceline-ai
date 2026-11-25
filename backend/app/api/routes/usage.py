from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import AuthContext, get_db, require_scope
from app.db.base import Database
from app.schemas.usage import UsageSummaryResponse
from app.services import usage_service

router = APIRouter(prefix="/v1/usage", tags=["usage"])


@router.get("", response_model=UsageSummaryResponse)
async def get_usage(
    auth: AuthContext = Depends(require_scope("invoices:read")),
    db: Database = Depends(get_db),
) -> UsageSummaryResponse:
    return await usage_service.get_usage_summary(db, auth.organisation_id)
