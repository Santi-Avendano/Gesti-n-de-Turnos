"""Availability grid + exceptions CRUD.

Grid replacement is atomic: DELETE all org rules and INSERT the new ones in
a single transaction. Existing bookings are NOT touched — they are confirmed
contracts and remain valid even if no longer matching the grid.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, time

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.models import AvailabilityRule, Exception_, ExceptionKind


# ---------- grid ----------

async def list_grid(db: AsyncSession, *, organization_id: int) -> list[AvailabilityRule]:
    rows = (
        await db.execute(
            select(AvailabilityRule)
            .where(AvailabilityRule.organization_id == organization_id)
            .order_by(AvailabilityRule.day_of_week, AvailabilityRule.start_local_time)
        )
    ).scalars().all()
    return list(rows)


async def replace_grid(
    db: AsyncSession,
    *,
    organization_id: int,
    rules: Sequence[tuple[int, time, time]] | Sequence[AvailabilityRule],
) -> list[AvailabilityRule]:
    """Atomically replace the grid for an organization.

    Validates that overlapping rules within the same day are rejected — two rules
    `09:00-13:00` and `12:00-18:00` would produce duplicate slots otherwise.
    """
    materialized: list[AvailabilityRule] = []
    by_day: dict[int, list[tuple[time, time]]] = {}
    for r in rules:
        if isinstance(r, AvailabilityRule):
            dow, sl, el = r.day_of_week, r.start_local_time, r.end_local_time
        else:
            dow, sl, el = r
        if el <= sl:
            raise ValidationError("end_local_time must be after start_local_time")
        by_day.setdefault(dow, []).append((sl, el))

    for dow, windows in by_day.items():
        windows.sort()
        for i in range(1, len(windows)):
            prev_end = windows[i - 1][1]
            cur_start = windows[i][0]
            if cur_start < prev_end:
                raise ValidationError(f"overlapping rules for day_of_week={dow}")

    await db.execute(
        delete(AvailabilityRule).where(AvailabilityRule.organization_id == organization_id)
    )

    for dow, windows in by_day.items():
        for sl, el in windows:
            row = AvailabilityRule(
                organization_id=organization_id,
                day_of_week=dow,
                start_local_time=sl,
                end_local_time=el,
            )
            db.add(row)
            materialized.append(row)

    await db.commit()
    for row in materialized:
        await db.refresh(row)
    return materialized


# ---------- exceptions ----------

async def list_exceptions(
    db: AsyncSession,
    *,
    organization_id: int,
    from_at: datetime | None = None,
    to_at: datetime | None = None,
) -> list[Exception_]:
    stmt = select(Exception_).where(Exception_.organization_id == organization_id)
    if from_at is not None:
        stmt = stmt.where(Exception_.end_at_utc > from_at)
    if to_at is not None:
        stmt = stmt.where(Exception_.start_at_utc < to_at)
    stmt = stmt.order_by(Exception_.start_at_utc)
    return list((await db.execute(stmt)).scalars().all())


async def get_exception(
    db: AsyncSession, *, organization_id: int, exception_id: int
) -> Exception_:
    row = (
        await db.execute(
            select(Exception_).where(
                Exception_.id == exception_id,
                Exception_.organization_id == organization_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("exception not found")
    return row


async def create_exception(
    db: AsyncSession,
    *,
    organization_id: int,
    start_at_utc: datetime,
    end_at_utc: datetime,
    kind: ExceptionKind,
    reason: str | None,
) -> Exception_:
    if end_at_utc <= start_at_utc:
        raise ValidationError("end_at_utc must be after start_at_utc")
    row = Exception_(
        organization_id=organization_id,
        start_at_utc=start_at_utc,
        end_at_utc=end_at_utc,
        kind=kind,
        reason=reason,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def update_exception(
    db: AsyncSession,
    *,
    organization_id: int,
    exception_id: int,
    start_at_utc: datetime | None = None,
    end_at_utc: datetime | None = None,
    kind: ExceptionKind | None = None,
    reason: str | None = None,
) -> Exception_:
    row = await get_exception(db, organization_id=organization_id, exception_id=exception_id)
    if start_at_utc is not None:
        row.start_at_utc = start_at_utc
    if end_at_utc is not None:
        row.end_at_utc = end_at_utc
    if kind is not None:
        row.kind = kind
    if reason is not None:
        row.reason = reason
    if row.end_at_utc <= row.start_at_utc:
        raise ValidationError("end_at_utc must be after start_at_utc")
    await db.commit()
    await db.refresh(row)
    return row


async def delete_exception(
    db: AsyncSession, *, organization_id: int, exception_id: int
) -> None:
    row = await get_exception(db, organization_id=organization_id, exception_id=exception_id)
    await db.delete(row)
    await db.commit()
