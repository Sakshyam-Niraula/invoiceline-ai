from __future__ import annotations

from app.core.crypto import generate_id, hash_password, utc_now_iso
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.db.base import Database, Statement
from app.schemas.auth import (
    AddMemberRequest,
    MemberResponse,
    OrganisationSummary,
    RoleType,
    UpdateOrganisationRequest,
)
from app.services.audit_service import build_audit_statement, record_audit


async def get_organisation(db: Database, organisation_id: str) -> OrganisationSummary:
    row = await db.fetchone(
        "SELECT id, name, slug, retention_days FROM organisations WHERE id = ?",
        (organisation_id,),
    )
    if row is None:
        raise NotFoundError("Organisation not found")
    return OrganisationSummary(
        id=str(row["id"]),
        name=str(row["name"]),
        slug=str(row["slug"]),
        retention_days=int(row["retention_days"]),
    )


async def update_organisation(
    db: Database,
    organisation_id: str,
    actor_id: str | None,
    payload: UpdateOrganisationRequest,
    request_id: str | None = None,
) -> OrganisationSummary:
    current = await get_organisation(db, organisation_id)
    new_name = payload.name.strip() if payload.name is not None else current.name
    new_retention = payload.retention_days if payload.retention_days is not None else current.retention_days

    await db.execute(
        "UPDATE organisations SET name = ?, retention_days = ?, updated_at = ? WHERE id = ?",
        (new_name, new_retention, utc_now_iso(), organisation_id),
    )
    await record_audit(
        db,
        organisation_id=organisation_id,
        actor_type="user",
        actor_id=actor_id,
        action="organisation.updated",
        resource_type="organisation",
        resource_id=organisation_id,
        metadata={"name": new_name, "retention_days": new_retention},
        request_id=request_id,
    )
    return OrganisationSummary(
        id=current.id,
        name=new_name,
        slug=current.slug,
        retention_days=new_retention,
    )


async def list_members(db: Database, organisation_id: str) -> list[MemberResponse]:
    rows = await db.fetchall(
        """
        SELECT m.id, m.user_id, u.email, u.full_name, m.role, m.created_at
        FROM memberships m
        JOIN users u ON u.id = m.user_id
        WHERE m.organisation_id = ?
        ORDER BY m.created_at ASC
        """,
        (organisation_id,),
    )
    return [
        MemberResponse(
            id=str(r["id"]),
            user_id=str(r["user_id"]),
            email=str(r["email"]),
            full_name=str(r["full_name"]),
            role=r["role"],
            created_at=str(r["created_at"]),
        )
        for r in rows
    ]


async def add_member(
    db: Database,
    organisation_id: str,
    actor_id: str | None,
    payload: AddMemberRequest,
    request_id: str | None = None,
) -> MemberResponse:
    existing_user = await db.fetchone("SELECT id FROM users WHERE email = ?", (payload.email,))
    if existing_user is not None:
        existing_mem = await db.fetchone(
            "SELECT id FROM memberships WHERE organisation_id = ? AND user_id = ?",
            (organisation_id, existing_user["id"]),
        )
        if existing_mem is not None:
            raise ConflictError("User is already a member of this organisation")
        raise ConflictError("An account with this email already exists in another organisation")

    now = utc_now_iso()
    user_id = generate_id("usr")
    membership_id = generate_id("mem")
    pw_hash = hash_password(payload.password)

    await db.batch(
        [
            Statement(
                sql="""
                INSERT INTO users (id, email, full_name, password_hash, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                params=(user_id, payload.email, payload.full_name.strip(), pw_hash, now, now),
            ),
            Statement(
                sql="""
                INSERT INTO memberships (id, organisation_id, user_id, role, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                params=(membership_id, organisation_id, user_id, payload.role, now),
            ),
            build_audit_statement(
                organisation_id=organisation_id,
                actor_type="user",
                actor_id=actor_id,
                action="member.added",
                resource_type="membership",
                resource_id=membership_id,
                metadata={"email": payload.email, "role": payload.role},
                request_id=request_id,
            ),
        ]
    )
    return MemberResponse(
        id=membership_id,
        user_id=user_id,
        email=payload.email,
        full_name=payload.full_name.strip(),
        role=payload.role,
        created_at=now,
    )


async def update_member_role(
    db: Database,
    organisation_id: str,
    membership_id: str,
    new_role: RoleType,
    actor_id: str | None,
    request_id: str | None = None,
) -> MemberResponse:
    row = await db.fetchone(
        """
        SELECT m.id, m.user_id, u.email, u.full_name, m.role, m.created_at
        FROM memberships m
        JOIN users u ON u.id = m.user_id
        WHERE m.id = ? AND m.organisation_id = ?
        """,
        (membership_id, organisation_id),
    )
    if row is None:
        raise NotFoundError("Member not found")

    if row["role"] == "owner" and new_role != "owner":
        owners = await db.fetchone(
            "SELECT COUNT(*) AS cnt FROM memberships WHERE organisation_id = ? AND role = 'owner'",
            (organisation_id,),
        )
        if owners and int(owners["cnt"]) <= 1:
            raise BadRequestError("Organisation must retain at least one owner")

    await db.execute(
        "UPDATE memberships SET role = ? WHERE id = ? AND organisation_id = ?",
        (new_role, membership_id, organisation_id),
    )
    await record_audit(
        db,
        organisation_id=organisation_id,
        actor_type="user",
        actor_id=actor_id,
        action="member.role_updated",
        resource_type="membership",
        resource_id=membership_id,
        metadata={"role": new_role},
        request_id=request_id,
    )
    return MemberResponse(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        email=str(row["email"]),
        full_name=str(row["full_name"]),
        role=new_role,
        created_at=str(row["created_at"]),
    )


async def remove_member(
    db: Database,
    organisation_id: str,
    membership_id: str,
    actor_id: str | None,
    request_id: str | None = None,
) -> None:
    row = await db.fetchone(
        "SELECT id, user_id, role FROM memberships WHERE id = ? AND organisation_id = ?",
        (membership_id, organisation_id),
    )
    if row is None:
        raise NotFoundError("Member not found")
    if row["role"] == "owner":
        raise BadRequestError("Cannot remove an organisation owner")

    await db.batch(
        [
            Statement(
                sql="DELETE FROM memberships WHERE id = ? AND organisation_id = ?",
                params=(membership_id, organisation_id),
            ),
            Statement(
                sql="UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND organisation_id = ?",
                params=(utc_now_iso(), row["user_id"], organisation_id),
            ),
            build_audit_statement(
                organisation_id=organisation_id,
                actor_type="user",
                actor_id=actor_id,
                action="member.removed",
                resource_type="membership",
                resource_id=membership_id,
                request_id=request_id,
            ),
        ]
    )
