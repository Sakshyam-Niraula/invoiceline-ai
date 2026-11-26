from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.core.crypto import (
    generate_id,
    generate_token,
    hash_password,
    hash_token,
    utc_now_iso,
    verify_password,
)
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, UnauthorizedError
from app.db.base import Database, Statement
from app.schemas.auth import (
    AuthSessionResponse,
    MeResponse,
    OrganisationSummary,
    RegisterRequest,
    RoleType,
    UserSummary,
)
from app.services.audit_service import build_audit_statement, record_audit

ROLE_PERMISSIONS: dict[RoleType, list[str]] = {
    "owner": [
        "invoices:read",
        "invoices:write",
        "invoices:review",
        "invoices:delete",
        "api_keys:manage",
        "webhooks:manage",
        "organisation:manage",
    ],
    "admin": [
        "invoices:read",
        "invoices:write",
        "invoices:review",
        "invoices:delete",
        "api_keys:manage",
        "webhooks:manage",
        "organisation:manage",
    ],
    "reviewer": [
        "invoices:read",
        "invoices:write",
        "invoices:review",
    ],
    "viewer": [
        "invoices:read",
    ],
}


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    base = base[:40] or "org"
    return f"{base}-{secrets.token_hex(3)}"


async def register_user(
    db: Database,
    settings: Settings,
    payload: RegisterRequest,
    ip_address: str | None = None,
    request_id: str | None = None,
) -> AuthSessionResponse:
    existing = await db.fetchone("SELECT id FROM users WHERE email = ?", (payload.email,))
    if existing is not None:
        raise ConflictError("An account with this email already exists", code="email_exists")

    now = utc_now_iso()
    expires_at = (datetime.now(UTC) + timedelta(hours=settings.session_ttl_hours)).isoformat()

    user_id = generate_id("usr")
    org_id = generate_id("org")
    membership_id = generate_id("mem")
    session_id = generate_id("ses")
    raw_token = generate_token("sess")
    token_hash = hash_token(raw_token)
    slug = _slugify(payload.organisation_name)
    pw_hash = hash_password(payload.password)

    statements = [
        Statement(
            sql="""
            INSERT INTO organisations (id, name, slug, retention_days, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            params=(org_id, payload.organisation_name.strip(), slug, 365, now, now),
        ),
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
            params=(membership_id, org_id, user_id, "owner", now),
        ),
        Statement(
            sql="""
            INSERT INTO sessions (id, user_id, organisation_id, token_hash, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            params=(session_id, user_id, org_id, token_hash, expires_at, now),
        ),
        build_audit_statement(
            organisation_id=org_id,
            actor_type="user",
            actor_id=user_id,
            action="auth.register",
            resource_type="organisation",
            resource_id=org_id,
            metadata={"email": payload.email},
            ip_address=ip_address,
            request_id=request_id,
        ),
    ]
    # D1's Python binding can fail to bridge a Python list of prepared
    # statements into the JavaScript batch API. Execute this small account
    # creation sequence individually and compensate on failure instead.
    try:
        for statement in statements:
            await db.execute(statement.sql, statement.params)
    except Exception:
        # Keep a failed signup from leaving a partial account behind.
        try:
            await db.execute("DELETE FROM organisations WHERE id = ?", (org_id,))
        except Exception:
            pass
        try:
            await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        except Exception:
            pass
        raise

    return AuthSessionResponse(
        access_token=raw_token,
        expires_at=expires_at,
        user=UserSummary(id=user_id, email=payload.email, full_name=payload.full_name.strip()),
        organisation=OrganisationSummary(
            id=org_id,
            name=payload.organisation_name.strip(),
            slug=slug,
            retention_days=365,
        ),
        role="owner",
    )


async def login_user(
    db: Database,
    settings: Settings,
    email: str,
    password: str,
    ip_address: str | None = None,
    request_id: str | None = None,
) -> AuthSessionResponse:
    user = await db.fetchone(
        "SELECT id, email, full_name, password_hash FROM users WHERE email = ?",
        (email,),
    )
    if user is None or not verify_password(password, str(user["password_hash"])):
        raise UnauthorizedError("Invalid email or password", code="invalid_credentials")

    membership = await db.fetchone(
        """
        SELECT m.role, o.id AS org_id, o.name AS org_name, o.slug AS org_slug, o.retention_days
        FROM memberships m
        JOIN organisations o ON o.id = m.organisation_id
        WHERE m.user_id = ?
        ORDER BY m.created_at ASC
        LIMIT 1
        """,
        (user["id"],),
    )
    if membership is None:
        raise ForbiddenError("User does not belong to any organisation")

    now = utc_now_iso()
    expires_at = (datetime.now(UTC) + timedelta(hours=settings.session_ttl_hours)).isoformat()
    session_id = generate_id("ses")
    raw_token = generate_token("sess")

    await db.batch(
        [
            Statement(
                sql="""
                INSERT INTO sessions (id, user_id, organisation_id, token_hash, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                params=(
                    session_id,
                    user["id"],
                    membership["org_id"],
                    hash_token(raw_token),
                    expires_at,
                    now,
                ),
            ),
            build_audit_statement(
                organisation_id=str(membership["org_id"]),
                actor_type="user",
                actor_id=str(user["id"]),
                action="auth.login",
                resource_type="session",
                resource_id=session_id,
                ip_address=ip_address,
                request_id=request_id,
            ),
        ]
    )

    role: RoleType = membership["role"]  # type: ignore[assignment]
    return AuthSessionResponse(
        access_token=raw_token,
        expires_at=expires_at,
        user=UserSummary(
            id=str(user["id"]),
            email=str(user["email"]),
            full_name=str(user["full_name"]),
        ),
        organisation=OrganisationSummary(
            id=str(membership["org_id"]),
            name=str(membership["org_name"]),
            slug=str(membership["org_slug"]),
            retention_days=int(membership["retention_days"]),
        ),
        role=role,
    )


async def logout_session(
    db: Database,
    session_id: str,
    organisation_id: str,
    user_id: str,
    ip_address: str | None = None,
    request_id: str | None = None,
) -> None:
    await db.execute(
        "UPDATE sessions SET revoked_at = ? WHERE id = ?",
        (utc_now_iso(), session_id),
    )
    await record_audit(
        db,
        organisation_id=organisation_id,
        actor_type="user",
        actor_id=user_id,
        action="auth.logout",
        resource_type="session",
        resource_id=session_id,
        ip_address=ip_address,
        request_id=request_id,
    )


async def request_password_reset(db: Database, email: str) -> str | None:
    """Create a single-use password reset token if user exists. Returns raw token or None."""
    user = await db.fetchone("SELECT id FROM users WHERE email = ?", (email,))
    if user is None:
        return None

    raw_token = generate_token("rst")
    now = utc_now_iso()
    expires_at = (datetime.now(UTC) + timedelta(minutes=30)).isoformat()
    await db.execute(
        """
        INSERT INTO password_reset_tokens (id, user_id, token_hash, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (generate_id("prt"), user["id"], hash_token(raw_token), expires_at, now),
    )
    return raw_token


async def confirm_password_reset(db: Database, raw_token: str, new_password: str) -> None:
    row = await db.fetchone(
        """
        SELECT id, user_id, expires_at, used_at
        FROM password_reset_tokens
        WHERE token_hash = ?
        """,
        (hash_token(raw_token),),
    )
    if row is None or row["used_at"] is not None:
        raise UnauthorizedError("Invalid or already used password reset token", code="invalid_reset_token")

    if str(row["expires_at"]) < utc_now_iso():
        raise UnauthorizedError("Password reset token has expired", code="expired_reset_token")

    now = utc_now_iso()
    new_hash = hash_password(new_password)
    await db.batch(
        [
            Statement(
                sql="UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                params=(new_hash, now, row["user_id"]),
            ),
            Statement(
                sql="UPDATE password_reset_tokens SET used_at = ? WHERE id = ?",
                params=(now, row["id"]),
            ),
            Statement(
                sql="UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                params=(now, row["user_id"]),
            ),
        ]
    )


async def get_me(db: Database, user_id: str, organisation_id: str) -> MeResponse:
    row = await db.fetchone(
        """
        SELECT
            u.id AS user_id, u.email, u.full_name,
            o.id AS org_id, o.name AS org_name, o.slug AS org_slug, o.retention_days,
            m.role
        FROM users u
        JOIN memberships m ON m.user_id = u.id
        JOIN organisations o ON o.id = m.organisation_id
        WHERE u.id = ? AND o.id = ?
        """,
        (user_id, organisation_id),
    )
    if row is None:
        raise NotFoundError("Account membership not found")
    role: RoleType = row["role"]  # type: ignore[assignment]
    return MeResponse(
        user=UserSummary(
            id=str(row["user_id"]),
            email=str(row["email"]),
            full_name=str(row["full_name"]),
        ),
        organisation=OrganisationSummary(
            id=str(row["org_id"]),
            name=str(row["org_name"]),
            slug=str(row["org_slug"]),
            retention_days=int(row["retention_days"]),
        ),
        role=role,
        permissions=ROLE_PERMISSIONS[role],
    )


async def provision_supabase_user(
    db: Database,
    settings: Settings,
    supabase_user: dict,
    access_token: str,
    request_id: str | None = None,
) -> AuthSessionResponse:
    """Link a verified Supabase identity to the existing InvoiceLine tenant model.

    Supabase owns the user's password. The legacy password_hash column is
    populated with a non-hash sentinel so this path never performs expensive
    password hashing in the Cloudflare Worker. verify_password rejects this
    value, so it cannot be used for local-password authentication.
    """
    email = str(supabase_user["email"]).strip().lower()
    metadata = supabase_user.get("user_metadata") or {}
    full_name = str(metadata.get("full_name") or metadata.get("name") or email.split("@")[0]).strip()[:120]
    organisation_name = str(metadata.get("organisation_name") or f"{full_name}'s workspace").strip()[:120]
    now = utc_now_iso()

    user = await db.fetchone("SELECT id, email, full_name FROM users WHERE email = ?", (email,))
    created_user_id: str | None = None
    created_org_id: str | None = None
    created_membership_id: str | None = None

    try:
        if user is None:
            user_id = generate_id("usr")
            await db.execute(
                "INSERT INTO users (id, email, full_name, password_hash, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, email, full_name, "supabase_managed", now, now),
            )
            created_user_id = user_id
            user = {"id": user_id, "email": email, "full_name": full_name}

        membership = await db.fetchone(
            """SELECT m.role, o.id AS org_id, o.name AS org_name, o.slug AS org_slug, o.retention_days
               FROM memberships m JOIN organisations o ON o.id = m.organisation_id
               WHERE m.user_id = ? ORDER BY m.created_at ASC LIMIT 1""",
            (user["id"],),
        )

        if membership is None:
            org_id = generate_id("org")
            slug = _slugify(organisation_name)
            await db.execute(
                "INSERT INTO organisations (id, name, slug, retention_days, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (org_id, organisation_name or "My workspace", slug, 365, now, now),
            )
            created_org_id = org_id

            membership_id = generate_id("mem")
            await db.execute(
                "INSERT INTO memberships (id, organisation_id, user_id, role, created_at) VALUES (?, ?, ?, ?, ?)",
                (membership_id, org_id, user["id"], "owner", now),
            )
            created_membership_id = membership_id
            membership = {
                "role": "owner",
                "org_id": org_id,
                "org_name": organisation_name or "My workspace",
                "org_slug": slug,
                "retention_days": 365,
            }
    except Exception:
        # D1 statements commit individually. Compensate for partial provisioning
        # so a retry can safely create a complete account.
        if created_membership_id is not None:
            try:
                await db.execute("DELETE FROM memberships WHERE id = ?", (created_membership_id,))
            except Exception:
                pass
        if created_org_id is not None:
            try:
                await db.execute("DELETE FROM organisations WHERE id = ?", (created_org_id,))
            except Exception:
                pass
        if created_user_id is not None:
            try:
                await db.execute("DELETE FROM users WHERE id = ?", (created_user_id,))
            except Exception:
                pass
        raise

    role: RoleType = membership["role"]
    expires_at = (datetime.now(UTC) + timedelta(hours=settings.session_ttl_hours)).isoformat()
    return AuthSessionResponse(
        access_token=access_token,
        expires_at=expires_at,
        user=UserSummary(id=str(user["id"]), email=email, full_name=str(user["full_name"])),
        organisation=OrganisationSummary(
            id=str(membership["org_id"]),
            name=str(membership["org_name"]),
            slug=str(membership["org_slug"]),
            retention_days=int(membership["retention_days"]),
        ),
        role=role,
    )
