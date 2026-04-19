from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentPrincipal, get_current_principal, require_admin
from app.db.session import get_db
from app.models import BookingStatus
from app.schemas.booking import (
    BookingCreate,
    BookingListResponse,
    BookingOut,
    BookingReschedule,
)
from app.services import booking_service

router = APIRouter(prefix="/bookings")


def _to_out(b) -> BookingOut:
    return BookingOut(
        id=b.id,
        user_id=b.user_id,
        start_at_utc=b.start_at_utc,
        end_at_utc=b.end_at_utc,
        status=b.status,
        created_at=b.created_at,
        cancelled_at=b.cancelled_at,
        cancelled_by_user_id=b.cancelled_by_user_id,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_booking(
    body: BookingCreate,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> BookingOut:
    booking = await booking_service.create_booking(
        db,
        organization_id=principal.org_id,
        user_id=principal.user_id,
        slot_start_at_utc=body.slot_start_at_utc,
    )
    return _to_out(booking)


@router.get("/me")
async def list_my_bookings(
    from_at: datetime | None = Query(default=None, alias="from"),
    to_at: datetime | None = Query(default=None, alias="to"),
    booking_status: BookingStatus | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> BookingListResponse:
    items, total = await booking_service.list_my_bookings(
        db,
        organization_id=principal.org_id,
        user_id=principal.user_id,
        from_at=from_at,
        to_at=to_at,
        status=booking_status,
        page=page,
        page_size=page_size,
    )
    return BookingListResponse(
        items=[_to_out(b) for b in items], total=total, page=page, page_size=page_size
    )


@router.get("")
async def list_org_bookings(
    from_at: datetime | None = Query(default=None, alias="from"),
    to_at: datetime | None = Query(default=None, alias="to"),
    user_id: int | None = Query(default=None),
    booking_status: BookingStatus | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    principal: CurrentPrincipal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> BookingListResponse:
    items, total = await booking_service.list_org_bookings(
        db,
        organization_id=principal.org_id,
        from_at=from_at,
        to_at=to_at,
        user_id=user_id,
        status=booking_status,
        page=page,
        page_size=page_size,
    )
    return BookingListResponse(
        items=[_to_out(b) for b in items], total=total, page=page, page_size=page_size
    )


@router.get("/{booking_id}")
async def get_booking(
    booking_id: int,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> BookingOut:
    booking = await booking_service.get_booking_for_actor(
        db,
        organization_id=principal.org_id,
        booking_id=booking_id,
        actor_user_id=principal.user_id,
        actor_role=principal.role,
    )
    return _to_out(booking)


@router.delete("/{booking_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_booking(
    booking_id: int,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> None:
    await booking_service.cancel_booking(
        db,
        organization_id=principal.org_id,
        actor_user_id=principal.user_id,
        actor_role=principal.role,
        booking_id=booking_id,
    )


@router.patch("/{booking_id}")
async def reschedule_booking(
    booking_id: int,
    body: BookingReschedule,
    principal: CurrentPrincipal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> BookingOut:
    booking = await booking_service.reschedule_booking(
        db,
        organization_id=principal.org_id,
        actor_user_id=principal.user_id,
        booking_id=booking_id,
        new_slot_start_at_utc=body.new_slot_start_at_utc,
    )
    return _to_out(booking)
