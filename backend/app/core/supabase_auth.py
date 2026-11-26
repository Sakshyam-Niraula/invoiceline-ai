from __future__ import annotations

import httpx

from app.config import Settings
from app.core.errors import UnauthorizedError


async def verify_supabase_access_token(token: str, settings: Settings) -> dict:
    """Validate a Supabase access token against Auth's user endpoint.

    The JWT is never trusted based on its decoded payload. Supabase Auth
    validates the signature, expiry, issuer and user status.
    """
    if not settings.supabase_url or not settings.supabase_publishable_key:
        raise RuntimeError("Supabase Auth is not configured")

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(
                f"{settings.supabase_auth_url}/auth/v1/user",
                headers={
                    "apikey": settings.supabase_auth_publishable_key,
                    "Authorization": f"Bearer {token}",
                },
            )
    except httpx.HTTPError as exc:
        raise UnauthorizedError("Unable to verify authentication session") from exc

    if response.status_code != 200:
        raise UnauthorizedError("Invalid or expired authentication session", code="invalid_session")

    user = response.json()
    if not isinstance(user, dict) or not user.get("id") or not user.get("email"):
        raise UnauthorizedError("Invalid authentication identity", code="invalid_session")
    return user
