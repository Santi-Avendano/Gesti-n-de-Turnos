from __future__ import annotations

import base64
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["dev", "test", "prod"] = "dev"
    app_debug: bool = False

    database_url: str = "postgresql+asyncpg://turnero:turnero@localhost:5432/turnero"

    jwt_private_key_pem_base64: SecretStr = SecretStr("")
    jwt_public_key_pem_base64: SecretStr = SecretStr("")
    jwt_algorithm: str = "RS256"
    jwt_access_ttl_minutes: int = 15
    jwt_refresh_ttl_days: int = 7
    jwt_issuer: str = "turnero"

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    def jwt_private_key_pem(self) -> bytes:
        encoded = self.jwt_private_key_pem_base64.get_secret_value()
        if not encoded:
            raise RuntimeError("JWT_PRIVATE_KEY_PEM_BASE64 is not configured")
        return base64.b64decode(encoded)

    def jwt_public_key_pem(self) -> bytes:
        encoded = self.jwt_public_key_pem_base64.get_secret_value()
        if not encoded:
            raise RuntimeError("JWT_PUBLIC_KEY_PEM_BASE64 is not configured")
        return base64.b64decode(encoded)


@lru_cache
def get_settings() -> Settings:
    return Settings()
