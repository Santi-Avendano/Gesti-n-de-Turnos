from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, EmailStr, Field, field_validator


SLUG_REGEX = r"^[a-z0-9](?:[a-z0-9-]{0,58}[a-z0-9])?$"


class AdminRegisterRequest(BaseModel):
    org_name: str = Field(min_length=1, max_length=120)
    org_slug: str = Field(pattern=SLUG_REGEX)
    timezone: str = Field(min_length=1, max_length=60)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {v}") from exc
        return v


class UserRegisterRequest(BaseModel):
    org_slug: str = Field(pattern=SLUG_REGEX)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    org_slug: str = Field(pattern=SLUG_REGEX)
    email: EmailStr
    password: str = Field(min_length=1)


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds until access_token expiry


class MeResponse(BaseModel):
    user_id: int
    email: EmailStr
    role: str
    organization_id: int
    organization_slug: str
    organization_timezone: str
