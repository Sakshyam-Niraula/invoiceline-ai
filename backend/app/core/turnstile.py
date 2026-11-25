from __future__ import annotations

import httpx

from app.core.errors import BadRequestError


async def verify_turnstile_token(
    secret_key: str,
    token: str | None,
    remote_ip: str | None = None,
) -> None:
    """Verify Cloudflare Turnstile token when Turnstile secret is configured."""
    if not secret_key:
        return
    if not token or not token.strip():
        raise BadRequestError("Verification challenge is required", code="turnstile_required")

    payload: dict[str, str] = {"secret": secret_key, "response": token.strip()}
    if remote_ip:
        payload["remoteip"] = remote_ip

    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data=payload,
        )
    if resp.status_code != 200:
        raise BadRequestError("Verification service unavailable", code="turnstile_failed")
    data = resp.json()
    if not data.get("success"):
        raise BadRequestError("Verification challenge failed", code="turnstile_failed")
