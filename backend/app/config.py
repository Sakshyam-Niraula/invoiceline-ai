from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Settings:
    environment: str
    app_secret: str
    database_path: str
    local_storage_path: str
    extraction_provider: str
    gemini_api_key: str
    gemini_model: str
    mistral_api_key: str
    turnstile_secret_key: str
    allowed_origins: tuple[str, ...]
    max_upload_bytes: int
    rate_limit_per_minute: int
    public_base_url: str
    session_ttl_hours: int
    signed_url_ttl_seconds: int
    supabase_url: str
    supabase_publishable_key: str
    supabase_secret_key: str
    supabase_bucket: str

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> Settings:
        env_name = str(raw.get("ENVIRONMENT", "development")).strip() or "development"
        app_secret = str(raw.get("APP_SECRET", "")).strip()
        if not app_secret or app_secret == "replace-with-32-byte-hex-secret-for-local-dev-only":
            if env_name.lower() == "production":
                raise RuntimeError("APP_SECRET must be configured in production")
            app_secret = "invoiceline-dev-only-secret-do-not-use-in-prod-32bytes"

        origins_raw = str(raw.get("ALLOWED_ORIGINS", "http://localhost:3000"))
        origins = tuple(o.strip() for o in origins_raw.split(",") if o.strip())

        return cls(
            environment=env_name,
            app_secret=app_secret,
            database_path=str(raw.get("DATABASE_PATH", "./local_dev.db")),
            local_storage_path=str(raw.get("LOCAL_STORAGE_PATH", "./local_storage")),
            extraction_provider=str(raw.get("EXTRACTION_PROVIDER", "gemini")).lower(),
            gemini_api_key=str(raw.get("GEMINI_API_KEY", "")).strip(),
            gemini_model=str(raw.get("GEMINI_MODEL", "gemini-2.5-flash")).strip(),
            mistral_api_key=str(raw.get("MISTRAL_API_KEY", "")).strip(),
            turnstile_secret_key=str(raw.get("TURNSTILE_SECRET_KEY", "")).strip(),
            allowed_origins=origins or ("http://localhost:3000",),
            max_upload_bytes=int(raw.get("MAX_UPLOAD_BYTES", 15 * 1024 * 1024)),
            rate_limit_per_minute=int(raw.get("RATE_LIMIT_PER_MINUTE", 120)),
            public_base_url=str(raw.get("PUBLIC_BASE_URL", "http://localhost:8000")).rstrip("/"),
            session_ttl_hours=int(raw.get("SESSION_TTL_HOURS", 24 * 7)),
            signed_url_ttl_seconds=int(raw.get("SIGNED_URL_TTL_SECONDS", 300)),
            supabase_url=str(raw.get("SUPABASE_URL", "")).rstrip("/"),
            supabase_publishable_key=str(raw.get("SUPABASE_PUBLISHABLE_KEY", "")).strip(),
            supabase_secret_key=str(raw.get("SUPABASE_SECRET_KEY", "")).strip(),
            supabase_bucket=str(raw.get("SUPABASE_BUCKET", "invoiceline-documents")).strip(),
        )


def load_worker_env(worker_env: Any) -> Settings:
    """Build Settings from a Cloudflare Worker env object."""
    mapping: dict[str, Any] = {}
    for key in (
        "ENVIRONMENT",
        "APP_SECRET",
        "EXTRACTION_PROVIDER",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "MISTRAL_API_KEY",
        "TURNSTILE_SECRET_KEY",
        "ALLOWED_ORIGINS",
        "MAX_UPLOAD_BYTES",
        "RATE_LIMIT_PER_MINUTE",
        "PUBLIC_BASE_URL",
        "SESSION_TTL_HOURS",
        "SIGNED_URL_TTL_SECONDS",
        "SUPABASE_URL",
        "SUPABASE_PUBLISHABLE_KEY",
        "SUPABASE_SECRET_KEY",
        "SUPABASE_BUCKET",
    ):
        val = getattr(worker_env, key, None)
        if val is not None:
            mapping[key] = val
    return Settings.from_mapping(mapping)


@lru_cache(maxsize=1)
def get_local_settings() -> Settings:
    """Load settings from process environment variables for local development."""
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            k, v = stripped.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
    return Settings.from_mapping(dict(os.environ))
