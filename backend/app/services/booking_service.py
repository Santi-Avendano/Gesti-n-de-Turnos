"""Booking lifecycle.

Concurrency model (RF-4.2):
    Two requests targeting the same slot will both call INSERT. The partial
    unique index `uniq_active_booking_slot` (organization_id, start_at_utc)
    WHERE status='active' is what guarantees only one wins. The loser raises
    `IntegrityError` here, which we map to a 409 — no SELECT FOR UPDATE, no
    advisory locks, no app-level mutex. The DB is the source of truth.

Validation pipeline before INSERT:
    1. Slot must be within booking horizon (org.booking_horizon_days)
    2. Slot must respect min_lead_minutes
    3. Slot must align with the org's grid for that day_of_week (in org TZ)
    4. Slot must not overlap any exception
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.core.time import local_to_utc_or_none
from app.models import (
    AvailabilityRule,
    Booking,
    BookingStatus,
    Exception_,
    Organization,
    UserRole,
)


# ---------- create ----------

async def create_booking(
    db: AsyncSession,
    *,
    organization_id: int,
    user_id: int,
    slot_start_at_utc: datetime,
) -> Booking:
    org = await _get_org(db, organization_id)
    duration = timedelta(minutes=org.slot_duration_minutes)
    slot_end_at_utc = slot_start_at_utc + duration

    _validate_slot_window(org, slot_start_at_utc)
    await _validate_against_grid(db, org, slot_start_at_utc, slot_end_at_utc)
    await _validate_against_exceptions(db, org, slot_start_at_utc, slot_end_at_utc)

    booking = Booking(
        organization_id=org.id,
        user_id=user_id,
        start_at_utc=slot_start_at_utc,
        end_at_utc=slot_end_at_utc,
        status=BookingStatus.ACTIVE,
    )
    db.add(booking)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("slot is no longer available") from exc

    await db.commit()
    await db.refresh(booking)
    return booking


# ---------- cancel ----------

async def cancel_booking(
    db: AsyncSession,
    *,
    organization_id: int,
    actor_user_id: int,
    actor_role: UserRole,
    booking_id: int,
    now_utc: datetime | None = None,
) -> Booking:
    """User can cancel only their own future bookings; admin can cancel any in their org."""
    booking = await _get_booking_in_org(db, organization_id, booking_id)

    if booking.status == BookingStatus.CANCELLED:
        raise ConflictError("booking already cancelled")

    is_owner = booking.user_id == actor_user_id
    is_admin = actor_role == UserRole.ADMIN
    if not (is_owner or is_admin):
        raise ForbiddenError("cannot cancel another user's booking")

    now = now_utc or datetime.now(UTC)
    if not is_admin and booking.start_at_utc <= now:
        # Users can only cancel future bookings.
        raise ForbiddenError("cannot cancel a past booking")

    booking.status = BookingStatus.CANCELLED
    booking.cancelled_at = now
    booking.cancelled_by_user_id = actor_user_id
    await db.commit()
    await db.refresh(booking)
    return booking


# ---------- admin reschedule ----------

async def reschedule_booking(
    db: AsyncSession,
    *,
    organization_id: int,
    actor_user_id: int,
    booking_id: int,
    new_slot_start_at_utc: datetime,
) -> Booking:
    """Admin-only: cancel current + create new at `new_slot_start_at_utc`, atomically.

    The cancellation lifts the partial unique index conflict for the original
    slot before we INSERT the new booking, so the previous (organization_id,
    start_at_utc) becomes free again.
    """
    booking = await _get_booking_in_org(db, organization_id, booking_id)
    if booking.status != BookingStatus.ACTIVE:
        raise ConflictError("only active bookings can be rescheduled")

    org = await _get_org(db, organization_id)
    duration = timedelta(minutes=org.slot_duration_minutes)
    new_end = new_slot_start_at_utc + duration

    _validate_slot_window(org, new_slot_start_at_utc)
    await _validate_against_grid(db, org, new_slot_start_at_utc, new_end)
    await _validate_against_exceptions(db, org, new_slot_start_at_utc, new_end)

    now = datetime.now(UTC)
    original_user_id = booking.user_id
    booking.status = BookingStatus.CANCELLED
    booking.cancelled_at = now
    booking.cancelled_by_user_id = actor_user_id
    await db.flush()

    new_booking = Booking(
        organization_id=org.id,
        user_id=original_user_id,
        start_at_utc=new_slot_start_at_utc,
        end_at_utc=new_end,
        status=BookingStatus.ACTIVE,
    )
    db.add(new_booking)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("target slot is no longer available") from exc

    await db.commit()
    await db.refresh(new_booking)
    return new_booking


# ---------- queries ----------

async def get_booking_for_actor(
    db: AsyncSession,
    *,
    organization_id: int,
    booking_id: int,
    actor_user_id: int,
    actor_role: UserRole,
) -> Booking:
    booking = await _get_booking_in_org(db, organization_id, booking_id)
    if actor_role != UserRole.ADMIN and booking.user_id != actor_user_id:
        # Don't reveal it exists for someone else; treat as 404.
        raise NotFoundError("booking not found")
    return booking


async def list_my_bookings(
    db: AsyncSession,
    *,
    organization_id: int,
    user_id: int,
    from_at: datetime | None,
    to_at: datetime | None,
    status: BookingStatus | None,
    page: int,
    page_size: int,
) -> tuple[list[Booking], int]:
    base = select(Booking).where(
        Booking.organization_id == organization_id,
        Booking.user_id == user_id,
    )
    if from_at is not None:
        base = base.where(Booking.end_at_utc > from_at)
    if to_at is not None:
        base = base.where(Booking.start_at_utc < to_at)
    if status is not None:
        base = base.where(Booking.status == status)

    total = (
        await db.execute(
            select(func.count()).select_from(base.subquery())
        )
    ).scalar_one()

    rows = (
        await db.execute(
            base.order_by(Booking.start_at_utc.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return list(rows), int(total)


async def list_org_bookings(
    db: AsyncSession,
    *,
    organization_id: int,
    from_at: datetime | None,
    to_at: datetime | None,
    user_id: int | None,
    status: BookingStatus | None,
    page: int,
    page_size: int,
) -> tuple[list[Booking], int]:
    base = select(Booking).where(Booking.organization_id == organization_id)
    if from_at is not None:
        base = base.where(Booking.end_at_utc > from_at)
    if to_at is not None:
        base = base.where(Booking.start_at_utc < to_at)
    if user_id is not None:
        base = base.where(Booking.user_id == user_id)
    if status is not None:
        base = base.where(Booking.status == status)

    total = (
        await db.execute(
            select(func.count()).select_from(base.subquery())
        )
    ).scalar_one()
    rows = (
        await db.execute(
            base.order_by(Booking.start_at_utc.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return list(rows), int(total)


# ---------- internals ----------

async def _get_org(db: AsyncSession, organization_id: int) -> Organization:
    org = (
        await db.execute(select(Organization).where(Organization.id == organization_id))
    ).scalar_one_or_none()
    if org is None:
        raise NotFoundError("organization not found")
    return org


async def _get_booking_in_org(
    db: AsyncSession, organization_id: int, booking_id: int
) -> Booking:
    booking = (
        await db.execute(
            select(Booking).where(
                Booking.id == booking_id,
                Booking.organization_id == organization_id,
            )
        )
    ).scalar_one_or_none()
    if booking is None:
        # 404 instead of 403 — never leak that the booking exists in another org.
        raise NotFoundError("booking not found")
    return booking


def _validate_slot_window(org: Organization, slot_start_at_utc: datetime) -> None:
    if slot_start_at_utc.tzinfo is None:
        raise ValidationError("slot_start_at_utc must be timezone-aware")

    now = datetime.now(UTC)
    lead = timedelta(minutes=org.min_lead_minutes)
    horizon = timedelta(days=org.booking_horizon_days)

    if slot_start_at_utc < now + lead:
        raise ValidationError("slot is too soon (min_lead_minutes not respected)")
    if slot_start_at_utc > now + horizon:
        raise ValidationError("slot is beyond the booking horizon")


async def _validate_against_grid(
    db: AsyncSession,
    org: Organization,
    slot_start_at_utc: datetime,
    slot_end_at_utc: datetime,
) -> None:
    """The slot must:
      * align to slot_duration_minutes from a rule's start_local_time
      * fit fully inside one of that day's rules (in org TZ).
    """
    tz = ZoneInfo(org.timezone)
    local_start = slot_start_at_utc.astimezone(tz)
    local_end = slot_end_at_utc.astimezone(tz)

    # Sanity: round-trip back to UTC must match — guards against DST gaps.
    expected_utc = local_to_utc_or_none(local_start.date(), local_start.time(), tz)
    if expected_utc != slot_start_at_utc:
        raise ValidationError("slot does not exist in organization timezone (DST gap)")

    dow = local_start.weekday()
    rules = (
        await db.execute(
            select(AvailabilityRule).where(
                AvailabilityRule.organization_id == org.id,
                AvailabilityRule.day_of_week == dow,
            )
        )
    ).scalars().all()

    if not rules:
        raise ValidationError("no availability for this day")

    duration_min = org.slot_duration_minutes
    slot_min_of_day = local_start.hour * 60 + local_start.minute
    slot_end_min = local_end.hour * 60 + local_end.minute
    # Cross-midnight guard: end must be on the same local date as start.
    if local_end.date() != local_start.date():
        raise ValidationError("slot crosses midnight in organization timezone")

    for rule in rules:
        rule_start_min = rule.start_local_time.hour * 60 + rule.start_local_time.minute
        rule_end_min = rule.end_local_time.hour * 60 + rule.end_local_time.minute
        offset = slot_min_of_day - rule_start_min
        if offset < 0 or offset % duration_min != 0:
            continue
        if slot_end_min > rule_end_min:
            continue
        return  # aligned and fits

    raise ValidationError("slot does not align with the availability grid")


async def _validate_against_exceptions(
    db: AsyncSession,
    org: Organization,
    slot_start_at_utc: datetime,
    slot_end_at_utc: datetime,
) -> None:
    """A slot overlapping any exception is invalid (full_day or range — same logic)."""
    overlapping = (
        await db.execute(
            select(Exception_.id)
            .where(
                Exception_.organization_id == org.id,
                Exception_.start_at_utc < slot_end_at_utc,
                Exception_.end_at_utc > slot_start_at_utc,
            )
            .limit(1)
        )
    ).first()
    if overlapping is not None:
        raise ValidationError("slot overlaps an exception")
