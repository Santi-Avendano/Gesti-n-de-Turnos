from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models import Organization


async def get_org(db: AsyncSession, *, organization_id: int) -> Organization:
    org = (
        await db.execute(select(Organization).where(Organization.id == organization_id))
    ).scalar_one_or_none()
    if org is None:
        raise NotFoundError("organization not found")
    return org


async def update_org(
    db: AsyncSession,
    *,
    organization_id: int,
    name: str | None = None,
    slot_duration_minutes: int | None = None,
    booking_horizon_days: int | None = None,
    min_lead_minutes: int | None = None,
) -> Organization:
    org = await get_org(db, organization_id=organization_id)
    if name is not None:
        org.name = name
    if slot_duration_minutes is not None:
        org.slot_duration_minutes = slot_duration_minutes
    if booking_horizon_days is not None:
        org.booking_horizon_days = booking_horizon_days
    if min_lead_minutes is not None:
        org.min_lead_minutes = min_lead_minutes
    await db.commit()
    await db.refresh(org)
    return org
