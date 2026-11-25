from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Request

from app.config import Settings
from app.core.crypto import hash_token, utc_now_iso
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.rate_limit import enforce_rate_limit
from app.db.base import Database
from app.schemas.auth import RoleType
from app.services.auth_service import ROLE_PERMISSIONS


@dataclass(frozen=True)
class AuthContext:
    organisation_id: str
    actor_type: str  # "user" | "api_key"
    user_id: str | None
    api_key_id: str | None
    session_id: str | None
    role: RoleType | None
    scopes: frozenset[str]
    request_id: str

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_storage(request: Request) -> Any:
    return request.app.state.storage


def get_queue(request: Request) -> Any:
    return request.app.state.queue


def get_request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "req_unknown"))


def _extract_bearer_or_cookie(request: Request) -> str | None:
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        if token:
            return token
    cookie_token = request.cookies.get("il_session")
    if cookie_token:
        return cookie_token.strip()
    return None


async def get_auth_context(
    request: Request,
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthContext:
    raw_token = _extract_bearer_or_cookie(request)
    if not raw_token:
        raise UnauthorizedError("Missing authentication credentials")

    req_id = get_request_id(request)
    token_hash = hash_token(raw_token)

    if raw_token.startswith("il_live_"):
        row = await db.fetchone(
            """
            SELECT id, organisation_id, scopes, revoked_at
            FROM api_keys
            WHERE key_hash = ?
            """,
            (token_hash,),
        )
        if row is None or row["revoked_at"] is not None:
            raise UnauthorizedError("Invalid or revoked API key", code="invalid_api_key")

        org_id = str(row["organisation_id"])
        key_id = str(row["id"])
        await enforce_rate_limit(db, f"org:{org_id}:key:{key_id}", settings.rate_limit_per_minute)
        await db.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (utc_now_iso(), key_id))
        scopes = frozenset(s for s in str(row["scopes"]).split(",") if s)
        ctx = AuthContext(
            organisation_id=org_id,
            actor_type="api_key",
            user_id=None,
            api_key_id=key_id,
            session_id=None,
            role=None,
            scopes=scopes,
            request_id=req_id,
        )
        request.state.auth = ctx
        return ctx

    row = await db.fetchone(
        """
        SELECT s.id AS session_id, s.user_id, s.organisation_id, s.expires_at, s.revoked_at, m.role
        FROM sessions s
        JOIN memberships m ON m.user_id = s.user_id AND m.organisation_id = s.organisation_id
        WHERE s.token_hash = ?
        """,
        (token_hash,),
    )
    if row is None or row["revoked_at"] is not None:
        raise UnauthorizedError("Invalid or revoked session", code="invalid_session")
    if str(row["expires_at"]) < utc_now_iso():
        raise UnauthorizedError("Session has expired", code="expired_session")

    org_id = str(row["organisation_id"])
    user_id = str(row["user_id"])
    role: RoleType = row["role"]
    await enforce_rate_limit(db, f"org:{org_id}:user:{user_id}", settings.rate_limit_per_minute)
    ctx = AuthContext(
        organisation_id=org_id,
        actor_type="user",
        user_id=user_id,
        api_key_id=None,
        session_id=str(row["session_id"]),
        role=role,
        scopes=frozenset(ROLE_PERMISSIONS[role]),
        request_id=req_id,
    )
    request.state.auth = ctx
    return ctx


def require_scope(scope: str) -> Callable[..., AuthContext]:
    async def _dependency(auth: AuthContext = Depends(get_auth_context)) -> AuthContext:
        if not auth.has_scope(scope):
            raise ForbiddenError(f"Missing required permission: {scope}")
        return auth

    return _dependency


def require_user_role(*allowed_roles: RoleType) -> Callable[..., AuthContext]:
    async def _dependency(auth: AuthContext = Depends(get_auth_context)) -> AuthContext:
        if auth.actor_type != "user" or auth.role not in allowed_roles:
            raise ForbiddenError("This action requires an authorised user account")
        return auth

    return _dependency
