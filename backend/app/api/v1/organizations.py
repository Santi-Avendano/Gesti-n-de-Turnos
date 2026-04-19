from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentPrincipal, get_current_principal, require_admin
from app.db.session import get_db
from app.schemas.organization import OrgPatch, OrgResponse
from app.services import organization_service

router = APIRouter()


def _to_response(org) -> OrgResponse:
    return OrgResponse(
        id=org.id,
        name=org.name,
        slug=org.slug,
        timezone=org.timezone,
        slot_duration_minutes=org.slot_duration_minutes,
        booking_horizon_days=org.booking_horizon_days,
        min_lead_minutes=org.min_lead_minutes,
        created_at=org.created_at,
    )


@router.get("/me")
async def get_my_org(
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> OrgResponse:
    org = await organization_service.get_org(db, organization_id=principal.org_id)
    return _to_response(org)


@router.patch("/me")
async def patch_my_org(
    body: OrgPatch,
    principal: CurrentPrincipal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> OrgResponse:
    org = await organization_service.update_org(
        db,
        organization_id=principal.org_id,
        name=body.name,
        slot_duration_minutes=body.slot_duration_minutes,
        booking_horizon_days=body.booking_horizon_days,
        min_lead_minutes=body.min_lead_minutes,
    )
    return _to_response(org)
