from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

RoleType = Literal["owner", "admin", "reviewer", "viewer"]


class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=254)
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str = Field(..., min_length=2, max_length=120)
    organisation_name: str = Field(..., min_length=2, max_length=120)
    turnstile_token: str | None = None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if "@" not in cleaned or "." not in cleaned.split("@", 1)[1]:
            raise ValueError("Invalid email address")
        return cleaned


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=254)
    password: str = Field(..., min_length=1, max_length=128)
    turnstile_token: str | None = None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.strip().lower()


class PasswordResetRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=254)
    turnstile_token: str | None = None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.strip().lower()


class PasswordResetConfirmRequest(BaseModel):
    token: str = Field(..., min_length=10, max_length=256)
    new_password: str = Field(..., min_length=8, max_length=128)


class UserSummary(BaseModel):
    id: str
    email: str
    full_name: str


class OrganisationSummary(BaseModel):
    id: str
    name: str
    slug: str
    retention_days: int


class AuthSessionResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: str
    user: UserSummary
    organisation: OrganisationSummary
    role: RoleType


class MeResponse(BaseModel):
    user: UserSummary
    organisation: OrganisationSummary
    role: RoleType
    permissions: list[str]


class UpdateOrganisationRequest(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    retention_days: int | None = Field(default=None, ge=30, le=3650)


class MemberResponse(BaseModel):
    id: str
    user_id: str
    email: str
    full_name: str
    role: RoleType
    created_at: str


class AddMemberRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=254)
    full_name: str = Field(..., min_length=2, max_length=120)
    password: str = Field(..., min_length=8, max_length=128)
    role: RoleType = "reviewer"

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if "@" not in cleaned or "." not in cleaned.split("@", 1)[1]:
            raise ValueError("Invalid email address")
        return cleaned


class UpdateMemberRoleRequest(BaseModel):
    role: RoleType


class CreateApiKeyRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)
    scopes: list[str] = Field(default_factory=lambda: ["invoices:read", "invoices:write"])


class ApiKeyResponse(BaseModel):
    id: str
    name: str
    key_prefix: str
    scopes: list[str]
    last_used_at: str | None
    revoked_at: str | None
    created_at: str


class CreateApiKeyResponse(ApiKeyResponse):
    secret_key: str
