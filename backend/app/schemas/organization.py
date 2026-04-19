from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class OrgResponse(BaseModel):
    id: int
    name: str
    slug: str
    timezone: str
    slot_duration_minutes: int
    booking_horizon_days: int
    min_lead_minutes: int
    created_at: datetime


class OrgPatch(BaseModel):
    """Partial update.

    `timezone` is intentionally NOT settable: switching timezones would shift
    every existing booking's local time and silently break user expectations.
    Run a manual migration if you ever need this.
    """

    name: str | None = Field(default=None, min_length=1, max_length=120)
    slot_duration_minutes: int | None = Field(default=None, gt=0, le=600)
    booking_horizon_days: int | None = Field(default=None, gt=0, le=730)
    min_lead_minutes: int | None = Field(default=None, ge=0, le=10_080)
