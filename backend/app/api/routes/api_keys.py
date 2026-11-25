from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import AuthContext, get_db, require_user_role
from app.db.base import Database
from app.schemas.auth import ApiKeyResponse, CreateApiKeyRequest, CreateApiKeyResponse
from app.services import api_key_service

router = APIRouter(prefix="/v1/api-keys", tags=["api-keys"])


@router.get("", response_model=list[ApiKeyResponse])
async def list_api_keys(
    auth: AuthContext = Depends(require_user_role("owner", "admin")),
    db: Database = Depends(get_db),
) -> list[ApiKeyResponse]:
    return await api_key_service.list_api_keys(db, auth.organisation_id)


@router.post("", response_model=CreateApiKeyResponse, status_code=201)
async def create_api_key(
    payload: CreateApiKeyRequest,
    auth: AuthContext = Depends(require_user_role("owner", "admin")),
    db: Database = Depends(get_db),
) -> CreateApiKeyResponse:
    return await api_key_service.create_api_key(
        db,
        auth.organisation_id,
        auth.user_id or "",
        payload,
        request_id=auth.request_id,
    )


@router.delete("/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: str,
    auth: AuthContext = Depends(require_user_role("owner", "admin")),
    db: Database = Depends(get_db),
) -> None:
    await api_key_service.revoke_api_key(
        db,
        auth.organisation_id,
        key_id,
        auth.user_id or "",
        request_id=auth.request_id,
    )
