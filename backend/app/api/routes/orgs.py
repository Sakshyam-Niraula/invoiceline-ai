from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import AuthContext, get_db, require_user_role
from app.db.base import Database
from app.schemas.auth import (
    AddMemberRequest,
    MemberResponse,
    OrganisationSummary,
    UpdateMemberRoleRequest,
    UpdateOrganisationRequest,
)
from app.services import org_service

router = APIRouter(prefix="/v1/organisation", tags=["organisation"])


@router.get("", response_model=OrganisationSummary)
async def get_organisation(
    auth: AuthContext = Depends(require_user_role("owner", "admin", "reviewer", "viewer")),
    db: Database = Depends(get_db),
) -> OrganisationSummary:
    return await org_service.get_organisation(db, auth.organisation_id)


@router.patch("", response_model=OrganisationSummary)
async def update_organisation(
    payload: UpdateOrganisationRequest,
    auth: AuthContext = Depends(require_user_role("owner", "admin")),
    db: Database = Depends(get_db),
) -> OrganisationSummary:
    return await org_service.update_organisation(
        db,
        auth.organisation_id,
        auth.user_id,
        payload,
        request_id=auth.request_id,
    )


@router.get("/members", response_model=list[MemberResponse])
async def list_members(
    auth: AuthContext = Depends(require_user_role("owner", "admin", "reviewer", "viewer")),
    db: Database = Depends(get_db),
) -> list[MemberResponse]:
    return await org_service.list_members(db, auth.organisation_id)


@router.post("/members", response_model=MemberResponse, status_code=201)
async def add_member(
    payload: AddMemberRequest,
    auth: AuthContext = Depends(require_user_role("owner", "admin")),
    db: Database = Depends(get_db),
) -> MemberResponse:
    return await org_service.add_member(
        db,
        auth.organisation_id,
        auth.user_id,
        payload,
        request_id=auth.request_id,
    )


@router.patch("/members/{membership_id}", response_model=MemberResponse)
async def update_member_role(
    membership_id: str,
    payload: UpdateMemberRoleRequest,
    auth: AuthContext = Depends(require_user_role("owner", "admin")),
    db: Database = Depends(get_db),
) -> MemberResponse:
    return await org_service.update_member_role(
        db,
        auth.organisation_id,
        membership_id,
        payload.role,
        auth.user_id,
        request_id=auth.request_id,
    )


@router.delete("/members/{membership_id}", status_code=204)
async def remove_member(
    membership_id: str,
    auth: AuthContext = Depends(require_user_role("owner", "admin")),
    db: Database = Depends(get_db),
) -> None:
    await org_service.remove_member(
        db,
        auth.organisation_id,
        membership_id,
        auth.user_id,
        request_id=auth.request_id,
    )
