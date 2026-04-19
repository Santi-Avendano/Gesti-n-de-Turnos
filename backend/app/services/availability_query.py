"""Composes DB lookups + the pure `slot_service.compute_available_slots`.

Kept separate from `slot_service` so the algorithm core stays DB-free and trivially testable.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.models import (
    AvailabilityRule,
    Booking,
    BookingStatus,
    Exception_,
    Organization,
    User,
)
from app.services.slot_service import (
    BookedRange,
    ExceptionRange,
    GridRule,
    Slot,
    compute_available_slots,
)


async def _get_org(db: AsyncSession, organization_id: int) -> Organization:
    org = (
        await db.execute(select(Organization).where(Organization.id == organization_id))
    ).scalar_one_or_none()
    if org is None:
        raise NotFoundError("organization not found")
    return org


def _validate_range(org: Organization, from_date: date, to_date: date) -> None:
    if to_date < from_date:
        raise ValidationError("'to' must be on or after 'from'")
    span = (to_date - from_date).days
    if span > org.booking_horizon_days:
        raise ValidationError(
            f"requested range exceeds booking horizon ({org.booking_horizon_days} days)"
        )


async def list_available_slots(
    db: AsyncSession,
    *,
    organization_id: int,
    from_date: date,
    to_date: date,
    now_utc: datetime | None = None,
) -> list[Slot]:
    org = await _get_org(db, organization_id)
    _validate_range(org, from_date, to_date)

    rules = (
        await db.execute(
            select(AvailabilityRule).where(AvailabilityRule.organization_id == org.id)
        )
    ).scalars().all()

    # Range with a 1-day pad so cross-DST or boundary slots aren't accidentally pruned.
    range_start = datetime.combine(from_date, datetime.min.time(), tzinfo=UTC) - timedelta(days=1)
    range_end = datetime.combine(to_date, datetime.max.time(), tzinfo=UTC) + timedelta(days=1)

    bookings = (
        await db.execute(
            select(Booking).where(
                Booking.organization_id == org.id,
                Booking.status == BookingStatus.ACTIVE,
                Booking.end_at_utc > range_start,
                Booking.start_at_utc < range_end,
            )
        )
    ).scalars().all()

    exceptions = (
        await db.execute(
            select(Exception_).where(
                Exception_.organization_id == org.id,
                Exception_.end_at_utc > range_start,
                Exception_.start_at_utc < range_end,
            )
        )
    ).scalars().all()

    return compute_available_slots(
        timezone=org.timezone,
        slot_duration_minutes=org.slot_duration_minutes,
        rules=[
            GridRule(
                day_of_week=r.day_of_week,
                start_local_time=r.start_local_time,
                end_local_time=r.end_local_time,
            )
            for r in rules
        ],
        bookings=[
            BookedRange(start_at_utc=b.start_at_utc, end_at_utc=b.end_at_utc)
            for b in bookings
        ],
        exceptions=[
            ExceptionRange(start_at_utc=e.start_at_utc, end_at_utc=e.end_at_utc)
            for e in exceptions
        ],
        from_date=from_date,
        to_date=to_date,
        now_utc=now_utc or datetime.now(UTC),
        min_lead_minutes=org.min_lead_minutes,
    )


async def admin_calendar(
    db: AsyncSession,
    *,
    organization_id: int,
    from_date: date,
    to_date: date,
    now_utc: datetime | None = None,
) -> tuple[list[Slot], list[tuple[Booking, str]]]:
    """Returns (free_slots, [(booking, user_email), ...]) for the admin calendar view."""
    free_slots = await list_available_slots(
        db,
        organization_id=organization_id,
        from_date=from_date,
        to_date=to_date,
        now_utc=now_utc,
    )

    range_start = datetime.combine(from_date, datetime.min.time(), tzinfo=UTC)
    range_end = datetime.combine(to_date, datetime.max.time(), tzinfo=UTC)

    rows = (
        await db.execute(
            select(Booking, User.email)
            .join(User, User.id == Booking.user_id)
            .where(
                Booking.organization_id == organization_id,
                Booking.start_at_utc < range_end,
                Booking.end_at_utc > range_start,
            )
            .order_by(Booking.start_at_utc)
        )
    ).all()

    return free_slots, [(b, email) for b, email in rows]
