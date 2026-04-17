"""Pure slot computation engine.

The function takes plain values (no SQLAlchemy, no Pydantic) so it can be
unit-tested without a database. Callers (the `/slots` endpoint, the booking
service) load org config + grid + bookings + exceptions and feed them in.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.core.time import local_to_utc_or_none


@dataclass(frozen=True, slots=True)
class GridRule:
    """One weekly availability window. Multiple rules per day_of_week are allowed
    (e.g., 9-12 + 14-18 to model a lunch break)."""

    day_of_week: int  # 0=Monday … 6=Sunday
    start_local_time: time
    end_local_time: time


@dataclass(frozen=True, slots=True)
class BookedRange:
    start_at_utc: datetime
    end_at_utc: datetime


@dataclass(frozen=True, slots=True)
class ExceptionRange:
    start_at_utc: datetime
    end_at_utc: datetime


@dataclass(frozen=True, slots=True)
class Slot:
    start_at_utc: datetime
    end_at_utc: datetime


def _ranges_overlap(
    a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime
) -> bool:
    return a_start < b_end and b_start < a_end


def _overlaps_any(
    slot_start: datetime,
    slot_end: datetime,
    ranges: Iterable[BookedRange | ExceptionRange],
) -> bool:
    return any(
        _ranges_overlap(slot_start, slot_end, r.start_at_utc, r.end_at_utc) for r in ranges
    )


def _index_rules(rules: Sequence[GridRule]) -> dict[int, list[GridRule]]:
    by_dow: dict[int, list[GridRule]] = defaultdict(list)
    for rule in rules:
        by_dow[rule.day_of_week].append(rule)
    return by_dow


def _iter_dates(from_date: date, to_date: date) -> Iterable[date]:
    if to_date < from_date:
        return
    cursor = from_date
    while cursor <= to_date:
        yield cursor
        cursor += timedelta(days=1)


def compute_available_slots(
    *,
    timezone: str,
    slot_duration_minutes: int,
    rules: Sequence[GridRule],
    bookings: Sequence[BookedRange],
    exceptions: Sequence[ExceptionRange],
    from_date: date,
    to_date: date,
    now_utc: datetime,
    min_lead_minutes: int = 0,
) -> list[Slot]:
    """Return the available slots in [from_date, to_date] (inclusive, in `timezone`).

    Excludes slots that:
      * fall on non-existent local times (DST spring-forward gap),
      * overlap any active booking,
      * overlap any exception,
      * start before `now_utc + min_lead_minutes`.
    """
    if slot_duration_minutes <= 0:
        raise ValueError("slot_duration_minutes must be positive")

    tz = ZoneInfo(timezone)
    duration = timedelta(minutes=slot_duration_minutes)
    threshold = now_utc + timedelta(minutes=min_lead_minutes)
    rules_by_dow = _index_rules(rules)

    out: list[Slot] = []
    for d in _iter_dates(from_date, to_date):
        dow = d.weekday()  # 0=Monday
        for rule in rules_by_dow.get(dow, ()):
            start_minutes = rule.start_local_time.hour * 60 + rule.start_local_time.minute
            end_minutes = rule.end_local_time.hour * 60 + rule.end_local_time.minute
            cursor = start_minutes
            while cursor + slot_duration_minutes <= end_minutes:
                slot_h, slot_m = divmod(cursor, 60)
                cursor += slot_duration_minutes
                slot_start_utc = local_to_utc_or_none(d, time(slot_h, slot_m), tz)
                if slot_start_utc is None:
                    continue  # DST gap — local time does not exist
                slot_end_utc = slot_start_utc + duration
                if slot_start_utc < threshold:
                    continue
                if _overlaps_any(slot_start_utc, slot_end_utc, bookings):
                    continue
                if _overlaps_any(slot_start_utc, slot_end_utc, exceptions):
                    continue
                out.append(Slot(start_at_utc=slot_start_utc, end_at_utc=slot_end_utc))
    out.sort(key=lambda s: s.start_at_utc)
    return out


__all__ = [
    "BookedRange",
    "ExceptionRange",
    "GridRule",
    "Slot",
    "compute_available_slots",
]
