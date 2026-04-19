from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.models import BookingStatus


class SlotOut(BaseModel):
    start_at_utc: datetime
    end_at_utc: datetime


class SlotListResponse(BaseModel):
    items: list[SlotOut]


class CalendarBookingOut(BaseModel):
    id: int
    user_id: int
    user_email: str
    start_at_utc: datetime
    end_at_utc: datetime
    status: BookingStatus


class CalendarResponse(BaseModel):
    free_slots: list[SlotOut]
    bookings: list[CalendarBookingOut]
