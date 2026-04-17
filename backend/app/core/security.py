"""Authentication primitives.

- Passwords: bcrypt (direct library, no passlib).
- Access tokens: JWT RS256 with claims {iss, sub, org_id, role, iat, exp}.
- Refresh tokens: 256-bit random URL-safe strings; stored as SHA-256 hashes
  with a `token_family_id` to detect reuse and revoke the entire family.

The plain refresh token never lives in the DB. Only the hash is persisted.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.core.config import Settings, get_settings


# ---------- passwords ----------

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


# ---------- access tokens ----------

@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    sub: int          # user_id
    org_id: int
    role: str         # "admin" | "user"


def encode_access_token(
    *, user_id: int, org_id: int, role: str, settings: Settings | None = None
) -> str:
    s = settings or get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "iss": s.jwt_issuer,
        "sub": str(user_id),
        "org_id": org_id,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=s.jwt_access_ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, s.jwt_private_key_pem(), algorithm=s.jwt_algorithm)


def decode_access_token(token: str, *, settings: Settings | None = None) -> AccessTokenClaims:
    s = settings or get_settings()
    payload = jwt.decode(
        token,
        s.jwt_public_key_pem(),
        algorithms=[s.jwt_algorithm],
        issuer=s.jwt_issuer,
        options={"require": ["exp", "iat", "iss", "sub"]},
    )
    return AccessTokenClaims(
        sub=int(payload["sub"]),
        org_id=int(payload["org_id"]),
        role=str(payload["role"]),
    )


# ---------- refresh tokens ----------

def generate_refresh_token_plain() -> str:
    """Cryptographically random URL-safe refresh token."""
    return secrets.token_urlsafe(32)


def hash_refresh_token(plain: str) -> str:
    """One-way hash for DB storage. Never reversible — lookup is by hash."""
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()
