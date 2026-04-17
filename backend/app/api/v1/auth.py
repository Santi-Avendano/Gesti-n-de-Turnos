from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentPrincipal, get_current_principal
from app.db.session import get_db
from app.schemas.auth import (
    AdminRegisterRequest,
    LoginRequest,
    LogoutRequest,
    MeResponse,
    RefreshRequest,
    TokenPair,
    UserRegisterRequest,
)
from app.services import auth_service

router = APIRouter()


@router.post("/admin/register", status_code=status.HTTP_201_CREATED)
async def admin_register(
    body: AdminRegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, int | str]:
    org, user = await auth_service.register_admin(
        db,
        org_name=body.org_name,
        org_slug=body.org_slug,
        timezone=body.timezone,
        email=body.email,
        password=body.password,
    )
    return {
        "organization_id": org.id,
        "organization_slug": org.slug,
        "user_id": user.id,
        "email": user.email,
    }


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def user_register(
    body: UserRegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, int | str]:
    user = await auth_service.register_user(
        db,
        org_slug=body.org_slug,
        email=body.email,
        password=body.password,
    )
    return {"user_id": user.id, "email": user.email}


@router.post("/login")
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenPair:
    access, refresh, expires_in = await auth_service.login(
        db, org_slug=body.org_slug, email=body.email, password=body.password
    )
    return TokenPair(access_token=access, refresh_token=refresh, expires_in=expires_in)


@router.post("/refresh")
async def refresh(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenPair:
    access, refresh_plain, expires_in = await auth_service.refresh(
        db, refresh_token_plain=body.refresh_token
    )
    return TokenPair(access_token=access, refresh_token=refresh_plain, expires_in=expires_in)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    body: LogoutRequest,
    db: AsyncSession = Depends(get_db),
) -> None:
    await auth_service.logout(db, refresh_token_plain=body.refresh_token)


@router.get("/me")
async def me(
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> MeResponse:
    user, org = await auth_service.get_user_with_org(db, user_id=principal.user_id)
    return MeResponse(
        user_id=user.id,
        email=user.email,
        role=user.role.value,
        organization_id=org.id,
        organization_slug=org.slug,
        organization_timezone=org.timezone,
    )
