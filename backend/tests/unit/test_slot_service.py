"""Tests for the slot computation engine — the heart of the system.

The function is intentionally pure (no DB, no async), so we can hammer it
with edge cases cheaply: DST transitions, exception overlaps, bookings,
lunch breaks, lead time, slot alignment.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.services.slot_service import (
    BookedRange,
    ExceptionRange,
    GridRule,
    Slot,
    compute_available_slots,
)

pytestmark = pytest.mark.unit


# ---------- helpers ----------

NEVER = datetime(1970, 1, 1, tzinfo=UTC)


def _rule(dow: int, start: tuple[int, int], end: tuple[int, int]) -> GridRule:
    return GridRule(
        day_of_week=dow,
        start_local_time=time(*start),
        end_local_time=time(*end),
    )


# ---------- baseline ----------

def test_simple_weekday_grid_emits_expected_slot_count() -> None:
    # Monday 2026-04-20 — Mon-Fri 9-18, 30min slots → 18 slots/day
    rules = [_rule(d, (9, 0), (18, 0)) for d in range(0, 5)]
    slots = compute_available_slots(
        timezone="America/Argentina/Buenos_Aires",
        slot_duration_minutes=30,
        rules=rules,
        bookings=[],
        exceptions=[],
        from_date=date(2026, 4, 20),  # Monday
        to_date=date(2026, 4, 20),
        now_utc=NEVER,
    )
    assert len(slots) == 18
    assert slots[0].start_at_utc == datetime(2026, 4, 20, 12, 0, tzinfo=UTC)  # 09:00 ART = 12:00 UTC
    assert slots[-1].start_at_utc == datetime(2026, 4, 20, 20, 30, tzinfo=UTC)  # 17:30 ART


def test_weekend_with_no_rules_emits_no_slots() -> None:
    rules = [_rule(d, (9, 0), (18, 0)) for d in range(0, 5)]  # Mon-Fri only
    slots = compute_available_slots(
        timezone="America/Argentina/Buenos_Aires",
        slot_duration_minutes=30,
        rules=rules,
        bookings=[],
        exceptions=[],
        from_date=date(2026, 4, 25),  # Saturday
        to_date=date(2026, 4, 26),    # Sunday
        now_utc=NEVER,
    )
    assert slots == []


def test_multiple_rules_same_day_produce_disjoint_windows() -> None:
    # Monday: 9-12 and 14-18, slot 60min → 3 + 4 = 7 slots
    rules = [_rule(0, (9, 0), (12, 0)), _rule(0, (14, 0), (18, 0))]
    slots = compute_available_slots(
        timezone="America/Argentina/Buenos_Aires",
        slot_duration_minutes=60,
        rules=rules,
        bookings=[],
        exceptions=[],
        from_date=date(2026, 4, 20),
        to_date=date(2026, 4, 20),
        now_utc=NEVER,
    )
    assert len(slots) == 7
    locals_ = [s.start_at_utc.hour for s in slots]
    # 9,10,11 ART = 12,13,14 UTC; 14,15,16,17 ART = 17,18,19,20 UTC
    assert locals_ == [12, 13, 14, 17, 18, 19, 20]


def test_slot_duration_not_dividing_window_drops_partial_tail() -> None:
    # 9:00-17:45, 30min slots → last slot starts 17:15 (ends 17:45) — 17 slots
    rules = [_rule(0, (9, 0), (17, 45))]
    slots = compute_available_slots(
        timezone="America/Argentina/Buenos_Aires",
        slot_duration_minutes=30,
        rules=rules,
        bookings=[],
        exceptions=[],
        from_date=date(2026, 4, 20),
        to_date=date(2026, 4, 20),
        now_utc=NEVER,
    )
    assert len(slots) == 17
    last = slots[-1]
    assert last.end_at_utc - last.start_at_utc == timedelta(minutes=30)


# ---------- exceptions ----------

def test_exception_full_day_drops_all_slots_for_that_day() -> None:
    rules = [_rule(0, (9, 0), (18, 0))]
    full_day_exc = ExceptionRange(
        start_at_utc=datetime(2026, 4, 20, 0, 0, tzinfo=UTC),
        end_at_utc=datetime(2026, 4, 21, 0, 0, tzinfo=UTC),
    )
    slots = compute_available_slots(
        timezone="America/Argentina/Buenos_Aires",
        slot_duration_minutes=30,
        rules=rules,
        bookings=[],
        exceptions=[full_day_exc],
        from_date=date(2026, 4, 20),
        to_date=date(2026, 4, 20),
        now_utc=NEVER,
    )
    assert slots == []


def test_exception_partial_overlap_drops_only_overlapping_slots() -> None:
    # Exception from 11:00 to 12:30 ART (= 14:00–15:30 UTC) drops slots starting at 11:00, 11:30, 12:00 ART
    rules = [_rule(0, (9, 0), (18, 0))]
    exc = ExceptionRange(
        start_at_utc=datetime(2026, 4, 20, 14, 0, tzinfo=UTC),
        end_at_utc=datetime(2026, 4, 20, 15, 30, tzinfo=UTC),
    )
    slots = compute_available_slots(
        timezone="America/Argentina/Buenos_Aires",
        slot_duration_minutes=30,
        rules=rules,
        bookings=[],
        exceptions=[exc],
        from_date=date(2026, 4, 20),
        to_date=date(2026, 4, 20),
        now_utc=NEVER,
    )
    starts_local_h_m = [(s.start_at_utc - timedelta(hours=-3)).strftime("%H:%M") for s in slots]
    # excluded: 11:00, 11:30, 12:00 (each overlaps the exception window)
    excluded = {"11:00", "11:30", "12:00"}
    assert excluded.isdisjoint(starts_local_h_m)
    assert len(slots) == 18 - 3


# ---------- bookings ----------

def test_active_booking_drops_exactly_that_slot() -> None:
    rules = [_rule(0, (9, 0), (18, 0))]
    booking = BookedRange(
        start_at_utc=datetime(2026, 4, 20, 13, 0, tzinfo=UTC),  # 10:00 ART
        end_at_utc=datetime(2026, 4, 20, 13, 30, tzinfo=UTC),
    )
    slots = compute_available_slots(
        timezone="America/Argentina/Buenos_Aires",
        slot_duration_minutes=30,
        rules=rules,
        bookings=[booking],
        exceptions=[],
        from_date=date(2026, 4, 20),
        to_date=date(2026, 4, 20),
        now_utc=NEVER,
    )
    assert booking.start_at_utc not in {s.start_at_utc for s in slots}
    assert len(slots) == 17


# ---------- past / lead time ----------

def test_past_slots_are_excluded() -> None:
    rules = [_rule(0, (9, 0), (18, 0))]
    # Now is 2026-04-20 14:00 UTC (= 11:00 ART) — slots before that should not appear
    now = datetime(2026, 4, 20, 14, 0, tzinfo=UTC)
    slots = compute_available_slots(
        timezone="America/Argentina/Buenos_Aires",
        slot_duration_minutes=30,
        rules=rules,
        bookings=[],
        exceptions=[],
        from_date=date(2026, 4, 20),
        to_date=date(2026, 4, 20),
        now_utc=now,
    )
    for s in slots:
        assert s.start_at_utc >= now


def test_min_lead_minutes_pushes_threshold_forward() -> None:
    rules = [_rule(0, (9, 0), (18, 0))]
    now = datetime(2026, 4, 20, 12, 0, tzinfo=UTC)  # 09:00 ART
    slots = compute_available_slots(
        timezone="America/Argentina/Buenos_Aires",
        slot_duration_minutes=30,
        rules=rules,
        bookings=[],
        exceptions=[],
        from_date=date(2026, 4, 20),
        to_date=date(2026, 4, 20),
        now_utc=now,
        min_lead_minutes=120,  # need 2h notice
    )
    threshold = now + timedelta(minutes=120)
    for s in slots:
        assert s.start_at_utc >= threshold


# ---------- DST ----------

def test_spring_forward_skips_nonexistent_local_times() -> None:
    """America/New_York 2024-03-10: clocks jump 02:00 → 03:00, the 02:00 hour does not exist.

    Grid 01:00-04:00 with 30min slots would naively produce 6 slots; we expect 4
    (01:00, 01:30, 03:00, 03:30 — the 02:00 and 02:30 slots are dropped as gaps).
    """
    rules = [_rule(6, (1, 0), (4, 0))]  # Sunday in our convention (Mon=0, Sun=6)
    slots = compute_available_slots(
        timezone="America/New_York",
        slot_duration_minutes=30,
        rules=rules,
        bookings=[],
        exceptions=[],
        from_date=date(2024, 3, 10),
        to_date=date(2024, 3, 10),
        now_utc=NEVER,
    )
    # Verify the survivors are exactly the four valid local times
    expected_starts_utc = {
        # 01:00 EST = 06:00 UTC, 01:30 EST = 06:30 UTC
        datetime(2024, 3, 10, 6, 0, tzinfo=UTC),
        datetime(2024, 3, 10, 6, 30, tzinfo=UTC),
        # 03:00 EDT = 07:00 UTC, 03:30 EDT = 07:30 UTC
        datetime(2024, 3, 10, 7, 0, tzinfo=UTC),
        datetime(2024, 3, 10, 7, 30, tzinfo=UTC),
    }
    assert {s.start_at_utc for s in slots} == expected_starts_utc


def test_fall_back_uses_first_occurrence_only() -> None:
    """America/New_York 2024-11-03: clocks fall 02:00 → 01:00, the 01:00 hour repeats.

    Grid 00:00-03:00 with 30min slots — we'd naively produce 6 slots in local
    time. Decision (documented): fold=0 → use the FIRST occurrence of ambiguous
    times. So we get 6 distinct UTC slot starts, all from the EDT-side mapping.
    """
    rules = [_rule(6, (0, 0), (3, 0))]  # Sunday
    slots = compute_available_slots(
        timezone="America/New_York",
        slot_duration_minutes=30,
        rules=rules,
        bookings=[],
        exceptions=[],
        from_date=date(2024, 11, 3),
        to_date=date(2024, 11, 3),
        now_utc=NEVER,
    )
    starts = sorted(s.start_at_utc for s in slots)
    assert len(starts) == 6
    # First occurrence of 01:00 local on 2024-11-03 is 01:00 EDT = 05:00 UTC.
    # Second occurrence (fold=1) would be 06:00 UTC — must NOT appear.
    assert datetime(2024, 11, 3, 5, 0, tzinfo=UTC) in starts
    assert datetime(2024, 11, 3, 6, 0, tzinfo=UTC) not in starts


def test_argentina_has_no_dst_so_slots_are_uniform() -> None:
    rules = [_rule(d, (9, 0), (18, 0)) for d in range(7)]
    slots = compute_available_slots(
        timezone="America/Argentina/Buenos_Aires",
        slot_duration_minutes=30,
        rules=rules,
        bookings=[],
        exceptions=[],
        from_date=date(2026, 4, 20),
        to_date=date(2026, 4, 26),  # 7 days
        now_utc=NEVER,
    )
    assert len(slots) == 18 * 7


# ---------- range ----------

def test_empty_date_range_returns_empty_list() -> None:
    rules = [_rule(0, (9, 0), (18, 0))]
    slots = compute_available_slots(
        timezone="America/Argentina/Buenos_Aires",
        slot_duration_minutes=30,
        rules=rules,
        bookings=[],
        exceptions=[],
        from_date=date(2026, 4, 25),
        to_date=date(2026, 4, 20),  # to < from
        now_utc=NEVER,
    )
    assert slots == []


def test_slot_returned_is_immutable_dataclass() -> None:
    slot = Slot(
        start_at_utc=datetime(2026, 4, 20, 12, 0, tzinfo=UTC),
        end_at_utc=datetime(2026, 4, 20, 12, 30, tzinfo=UTC),
    )
    with pytest.raises(Exception):  # frozen dataclass
        slot.start_at_utc = datetime(2027, 1, 1, tzinfo=UTC)  # type: ignore[misc]
