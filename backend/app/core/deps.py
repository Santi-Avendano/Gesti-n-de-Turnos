"""FastAPI dependencies.

`get_current_principal` is the single authentication checkpoint. Every
authenticated endpoint depends on it (or on `require_admin`, which adds a
role check). `org_id` always comes from the JWT claim — services NEVER
trust an `organization_id` from the URL or body.
"""
from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, status
from fastapi.exceptions import HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models import UserRole

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=True)


@dataclass(frozen=True, slots=True)
class CurrentPrincipal:
    user_id: int
    org_id: int
    role: UserRole


def _unauthorized(detail: str = "invalid token") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_principal(
    token: str = Depends(oauth2_scheme),
) -> CurrentPrincipal:
    try:
        claims = decode_access_token(token)
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorized("token expired") from exc
    except jwt.PyJWTError as exc:
        raise _unauthorized("invalid token") from exc

    try:
        role = UserRole(claims.role)
    except ValueError as exc:
        raise _unauthorized("invalid role") from exc

    return CurrentPrincipal(user_id=claims.sub, org_id=claims.org_id, role=role)


async def require_admin(
    principal: CurrentPrincipal = Depends(get_current_principal),
) -> CurrentPrincipal:
    if principal.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin only")
    return principal


# re-export for clarity
__all__ = [
    "CurrentPrincipal",
    "get_current_principal",
    "get_db",
    "oauth2_scheme",
    "require_admin",
]
_ = get_db  # silence unused-import linters
