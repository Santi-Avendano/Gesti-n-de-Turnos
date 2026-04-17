"""Authentication / registration logic.

The refresh-token rotation here implements **reuse detection**: if a previously
revoked refresh token is presented to /refresh, we revoke every token in its
family. This neutralizes a leaked refresh token even if the attacker's browser
session looked valid up to that point.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import ConflictError, NotFoundError, UnauthorizedError, ValidationError
from app.core.security import (
    encode_access_token,
    generate_refresh_token_plain,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models import Organization, RefreshToken, User, UserRole


# ---------- registration ----------

async def register_admin(
    db: AsyncSession,
    *,
    org_name: str,
    org_slug: str,
    timezone: str,
    email: str,
    password: str,
) -> tuple[Organization, User]:
    org = Organization(
        name=org_name,
        slug=org_slug,
        timezone=timezone,
    )
    db.add(org)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("org_slug already taken") from exc

    user = User(
        organization_id=org.id,
        email=email.lower(),
        password_hash=hash_password(password),
        role=UserRole.ADMIN,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("email already registered for this organization") from exc

    await db.commit()
    return org, user


async def register_user(
    db: AsyncSession,
    *,
    org_slug: str,
    email: str,
    password: str,
) -> User:
    org = (await db.execute(select(Organization).where(Organization.slug == org_slug))).scalar_one_or_none()
    if org is None:
        raise NotFoundError("organization not found")
    user = User(
        organization_id=org.id,
        email=email.lower(),
        password_hash=hash_password(password),
        role=UserRole.USER,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("email already registered for this organization") from exc
    await db.commit()
    return user


# ---------- login / refresh / logout ----------

async def _issue_token_pair(
    db: AsyncSession,
    *,
    user: User,
    family_id: uuid.UUID,
) -> tuple[str, str, int]:
    settings = get_settings()
    access_token = encode_access_token(
        user_id=user.id,
        org_id=user.organization_id,
        role=user.role.value,
        settings=settings,
    )
    plain_refresh = generate_refresh_token_plain()
    expires_at = datetime.now(UTC) + timedelta(days=settings.jwt_refresh_ttl_days)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_family_id=family_id,
            token_hash=hash_refresh_token(plain_refresh),
            expires_at=expires_at,
        )
    )
    await db.flush()
    expires_in = settings.jwt_access_ttl_minutes * 60
    return access_token, plain_refresh, expires_in


async def login(
    db: AsyncSession, *, org_slug: str, email: str, password: str
) -> tuple[str, str, int]:
    """Returns (access_token, refresh_token_plain, expires_in_seconds)."""
    user = await _find_user(db, org_slug=org_slug, email=email)
    if user is None or not verify_password(password, user.password_hash):
        raise UnauthorizedError("invalid credentials")
    family_id = uuid.uuid4()
    pair = await _issue_token_pair(db, user=user, family_id=family_id)
    await db.commit()
    return pair


async def refresh(
    db: AsyncSession, *, refresh_token_plain: str
) -> tuple[str, str, int]:
    token_hash = hash_refresh_token(refresh_token_plain)
    row = (
        await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    ).scalar_one_or_none()
    if row is None:
        raise UnauthorizedError("invalid refresh token")

    now = datetime.now(UTC)

    if row.revoked_at is not None:
        # ⚠️ REUSE DETECTED — revoke entire family.
        await db.execute(
            update(RefreshToken)
            .where(
                RefreshToken.token_family_id == row.token_family_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        await db.commit()
        raise UnauthorizedError("refresh token reuse detected — session revoked")

    if row.expires_at <= now:
        raise UnauthorizedError("refresh token expired")

    user = (await db.execute(select(User).where(User.id == row.user_id))).scalar_one_or_none()
    if user is None:
        raise UnauthorizedError("user no longer exists")

    # Rotate: revoke current, mint new pair under the same family.
    row.revoked_at = now
    pair = await _issue_token_pair(db, user=user, family_id=row.token_family_id)
    await db.commit()
    return pair


async def logout(db: AsyncSession, *, refresh_token_plain: str) -> None:
    token_hash = hash_refresh_token(refresh_token_plain)
    row = (
        await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    ).scalar_one_or_none()
    if row is None:
        # Be permissive — logging out an unknown token is a no-op (the client
        # forgot it anyway). Avoid leaking which tokens existed.
        return
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        await db.commit()


# ---------- helpers ----------

async def _find_user(
    db: AsyncSession, *, org_slug: str, email: str
) -> User | None:
    stmt = (
        select(User)
        .join(Organization, Organization.id == User.organization_id)
        .where(Organization.slug == org_slug, User.email == email.lower())
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_user_with_org(db: AsyncSession, *, user_id: int) -> tuple[User, Organization]:
    stmt = (
        select(User, Organization)
        .join(Organization, Organization.id == User.organization_id)
        .where(User.id == user_id)
    )
    row = (await db.execute(stmt)).one_or_none()
    if row is None:
        raise NotFoundError("user not found")
    return row[0], row[1]


# Defensive: ValidationError import keeps it available for callers; remove if unused.
_ = ValidationError
