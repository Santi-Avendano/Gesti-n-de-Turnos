from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentPrincipal, get_current_principal, require_admin
from app.db.session import get_db
from app.schemas.slot import CalendarBookingOut, CalendarResponse, SlotListResponse, SlotOut
from app.services import availability_query

router = APIRouter()


@router.get("/slots")
async def list_slots(
    from_date: date = Query(alias="from"),
    to_date: date = Query(alias="to"),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> SlotListResponse:
    slots = await availability_query.list_available_slots(
        db,
        organization_id=principal.org_id,
        from_date=from_date,
        to_date=to_date,
    )
    return SlotListResponse(
        items=[SlotOut(start_at_utc=s.start_at_utc, end_at_utc=s.end_at_utc) for s in slots]
    )


@router.get("/admin/calendar")
async def admin_calendar(
    from_date: date = Query(alias="from"),
    to_date: date = Query(alias="to"),
    principal: CurrentPrincipal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> CalendarResponse:
    free, bookings = await availability_query.admin_calendar(
        db,
        organization_id=principal.org_id,
        from_date=from_date,
        to_date=to_date,
    )
    return CalendarResponse(
        free_slots=[SlotOut(start_at_utc=s.start_at_utc, end_at_utc=s.end_at_utc) for s in free],
        bookings=[
            CalendarBookingOut(
                id=b.id,
                user_id=b.user_id,
                user_email=email,
                start_at_utc=b.start_at_utc,
                end_at_utc=b.end_at_utc,
                status=b.status,
            )
            for b, email in bookings
        ],
    )
