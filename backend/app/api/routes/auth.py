from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from app.api.deps import AuthContext, get_auth_context, get_db, get_request_id, get_settings
from app.config import Settings
from app.core.rate_limit import enforce_rate_limit
from app.core.supabase_auth import verify_supabase_access_token
from app.core.turnstile import verify_turnstile_token
from app.db.base import Database
from app.schemas.auth import (
    AuthSessionResponse,
    LoginRequest,
    MeResponse,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    RegisterRequest,
)
from app.services import auth_service

router = APIRouter(prefix="/v1/auth", tags=["auth"])


def _client_ip(request: Request) -> str | None:
    return request.headers.get("cf-connecting-ip") or (
        request.client.host if request.client else None
    )


def _set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        key="il_session",
        value=token,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )




@router.post("/provision", response_model=AuthSessionResponse)
async def provision_supabase_session(
    request: Request,
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthSessionResponse:
    authorization = request.headers.get("authorization", "")
    if not authorization.lower().startswith("bearer "):
        from app.core.errors import UnauthorizedError
        raise UnauthorizedError("Supabase access token required")
    token = authorization[7:].strip()
    supabase_user = await verify_supabase_access_token(token, settings)
    return await auth_service.provision_supabase_user(
        db, settings, supabase_user, token, request_id=get_request_id(request)
    )


@router.post("/register", response_model=AuthSessionResponse, status_code=201)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthSessionResponse:
    ip = _client_ip(request)
    await enforce_rate_limit(db, f"auth:register:{ip or 'unknown'}", 20)
    await verify_turnstile_token(settings.turnstile_secret_key, payload.turnstile_token, ip)
    session = await auth_service.register_user(
        db,
        settings,
        payload,
        ip_address=ip,
        request_id=get_request_id(request),
    )
    _set_session_cookie(response, session.access_token, settings)
    return session


@router.post("/login", response_model=AuthSessionResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthSessionResponse:
    ip = _client_ip(request)
    await enforce_rate_limit(db, f"auth:login:{ip or 'unknown'}", 30)
    await verify_turnstile_token(settings.turnstile_secret_key, payload.turnstile_token, ip)
    session = await auth_service.login_user(
        db,
        settings,
        payload.email,
        payload.password,
        ip_address=ip,
        request_id=get_request_id(request),
    )
    _set_session_cookie(response, session.access_token, settings)
    return session


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    auth: AuthContext = Depends(get_auth_context),
    db: Database = Depends(get_db),
) -> None:
    if auth.session_id and auth.user_id:
        await auth_service.logout_session(
            db,
            session_id=auth.session_id,
            organisation_id=auth.organisation_id,
            user_id=auth.user_id,
            ip_address=_client_ip(request),
            request_id=auth.request_id,
        )
    response.delete_cookie("il_session", path="/")


@router.get("/me", response_model=MeResponse)
async def me(
    auth: AuthContext = Depends(get_auth_context),
    db: Database = Depends(get_db),
) -> MeResponse:
    if not auth.user_id:
        from app.core.errors import ForbiddenError

        raise ForbiddenError("Session authentication required for /auth/me")
    return await auth_service.get_me(db, auth.user_id, auth.organisation_id)


@router.post("/password-reset/request")
async def request_password_reset(
    payload: PasswordResetRequest,
    request: Request,
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, str | None]:
    ip = _client_ip(request)
    await enforce_rate_limit(db, f"auth:reset:{ip or 'unknown'}", 15)
    await verify_turnstile_token(settings.turnstile_secret_key, payload.turnstile_token, ip)
    token = await auth_service.request_password_reset(db, payload.email)
    resp: dict[str, str | None] = {
        "message": "If an account exists for that email, reset instructions have been issued.",
    }
    if not settings.is_production and token is not None:
        resp["dev_reset_token"] = token
    return resp


@router.post("/password-reset/confirm")
async def confirm_password_reset(
    payload: PasswordResetConfirmRequest,
    db: Database = Depends(get_db),
) -> dict[str, str]:
    await auth_service.confirm_password_reset(db, payload.token, payload.new_password)
    return {"message": "Password has been reset successfully."}
