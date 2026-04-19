"""RF-4.2 — atomic booking concurrency.

Ten parallel tasks racing for the SAME slot must produce exactly one winner.
The remaining nine must see ConflictError(409) (mapped from PostgreSQL's
unique_violation on the partial index `uniq_active_booking_slot`).

We bypass the test session's SAVEPOINT machinery here because we need real
INSERTs visible to multiple concurrent sessions through the actual engine.
The fixture rolls back the outer transaction at teardown, but inside this
test we use a separate engine and explicitly clean up the rows we created.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.exceptions import AppError, ConflictError
from app.models import (
    AvailabilityRule,
    Booking,
    Organization,
    User,
    UserRole,
)
from app.services import booking_service
from app.core.security import hash_password

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def real_engine() -> AsyncEngine:
    """A second engine that talks to the same testcontainer Postgres but does
    NOT join the per-test SAVEPOINT transaction — we want true concurrency."""
    engine = create_async_engine(get_settings().database_url, future=True)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _setup_org_and_user(
    engine: AsyncEngine, *, slug: str
) -> tuple[int, int, datetime]:
    """Create org + admin user + grid rule covering the contested slot.

    Returns (organization_id, user_id, slot_start_at_utc).
    """
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    # Tomorrow at 12:00 UTC — well after min_lead, well before horizon.
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).date()
    slot_start = datetime.combine(tomorrow, time(12, 0), tzinfo=UTC)

    async with SessionLocal() as s:
        org = Organization(
            name=f"Race-{slug}",
            slug=slug,
            timezone="UTC",
            slot_duration_minutes=30,
            booking_horizon_days=60,
            min_lead_minutes=0,
        )
        s.add(org)
        await s.flush()

        user = User(
            organization_id=org.id,
            email=f"race-{slug}@x.com",
            password_hash=hash_password("secret123"),
            role=UserRole.ADMIN,
        )
        s.add(user)

        # Mon=0 … Sun=6 — cover the actual weekday of `tomorrow` so the slot is valid.
        s.add(
            AvailabilityRule(
                organization_id=org.id,
                day_of_week=tomorrow.weekday(),
                start_local_time=time(0, 0),
                end_local_time=time(23, 30),
            )
        )
        await s.commit()
        return org.id, user.id, slot_start


async def _cleanup(engine: AsyncEngine, organization_id: int) -> None:
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as s:
        await s.execute(delete(Booking).where(Booking.organization_id == organization_id))
        await s.execute(
            delete(AvailabilityRule).where(AvailabilityRule.organization_id == organization_id)
        )
        await s.execute(delete(User).where(User.organization_id == organization_id))
        await s.execute(delete(Organization).where(Organization.id == organization_id))
        await s.commit()


async def _attempt_booking(
    engine: AsyncEngine,
    *,
    organization_id: int,
    user_id: int,
    slot_start: datetime,
) -> str:
    """Try to book; return 'ok' on success, 'conflict' if 409, raise otherwise."""
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as session:
        try:
            await booking_service.create_booking(
                session,
                organization_id=organization_id,
                user_id=user_id,
                slot_start_at_utc=slot_start,
            )
        except ConflictError:
            return "conflict"
        except AppError:
            raise
    return "ok"


async def test_ten_parallel_bookings_yield_one_success(real_engine: AsyncEngine) -> None:
    """⭐ RF-4.2 — exactly 1×ok + 9×conflict, in any interleaving."""
    slug = f"race-{uuid.uuid4().hex[:8]}"
    organization_id, user_id, slot_start = await _setup_org_and_user(real_engine, slug=slug)

    try:
        results = await asyncio.gather(
            *(
                _attempt_booking(
                    real_engine,
                    organization_id=organization_id,
                    user_id=user_id,
                    slot_start=slot_start,
                )
                for _ in range(10)
            )
        )
        assert results.count("ok") == 1, results
        assert results.count("conflict") == 9, results
    finally:
        await _cleanup(real_engine, organization_id)


async def test_cancelled_booking_frees_the_slot(real_engine: AsyncEngine) -> None:
    """The partial unique index only constrains active rows.

    After cancelling a booking, the same slot must accept a new active one.
    """
    slug = f"reuse-{uuid.uuid4().hex[:8]}"
    organization_id, user_id, slot_start = await _setup_org_and_user(real_engine, slug=slug)

    SessionLocal = async_sessionmaker(real_engine, expire_on_commit=False)

    try:
        # First booking succeeds.
        async with SessionLocal() as s:
            first = await booking_service.create_booking(
                s,
                organization_id=organization_id,
                user_id=user_id,
                slot_start_at_utc=slot_start,
            )
            first_id = first.id

        # Cancel it.
        async with SessionLocal() as s:
            await booking_service.cancel_booking(
                s,
                organization_id=organization_id,
                actor_user_id=user_id,
                actor_role=UserRole.ADMIN,
                booking_id=first_id,
            )

        # Second booking on the same slot now succeeds.
        async with SessionLocal() as s:
            second = await booking_service.create_booking(
                s,
                organization_id=organization_id,
                user_id=user_id,
                slot_start_at_utc=slot_start,
            )
            assert second.id != first_id

        # Sanity: exactly two rows total, only the second is active.
        async with SessionLocal() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT count(*) FILTER (WHERE status='active') AS active, "
                        "count(*) AS total FROM bookings WHERE organization_id=:o"
                    ),
                    {"o": organization_id},
                )
            ).one()
            assert row.active == 1
            assert row.total == 2
    finally:
        await _cleanup(real_engine, organization_id)
