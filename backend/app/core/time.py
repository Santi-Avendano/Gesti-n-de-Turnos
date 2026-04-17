from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo


def local_to_utc_or_none(d: date, t: time, tz: ZoneInfo) -> datetime | None:
    """Convert a wall-clock (date, time) in tz to UTC.

    Returns None if the local time does not exist (DST spring-forward gap).
    For ambiguous times (DST fall-back), defaults to fold=0 (first occurrence).

    Detection of non-existent times: round-trip through UTC and back; zoneinfo
    silently normalizes gaps, so the round-tripped naive datetime differs from
    the original for non-existent inputs.
    """
    naive = datetime.combine(d, t)
    aware = naive.replace(tzinfo=tz)  # fold=0 by default
    utc = aware.astimezone(UTC)
    back = utc.astimezone(tz).replace(tzinfo=None)
    if back != naive:
        return None
    return utc
