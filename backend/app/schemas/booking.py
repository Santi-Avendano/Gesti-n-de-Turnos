from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.models import BookingStatus


class BookingCreate(BaseModel):
    slot_start_at_utc: datetime

    @model_validator(mode="after")
    def _tz_aware(self) -> "BookingCreate":
        if self.slot_start_at_utc.tzinfo is None:
            raise ValueError("slot_start_at_utc must be timezone-aware")
        return self


class BookingReschedule(BaseModel):
    new_slot_start_at_utc: datetime

    @model_validator(mode="after")
    def _tz_aware(self) -> "BookingReschedule":
        if self.new_slot_start_at_utc.tzinfo is None:
            raise ValueError("new_slot_start_at_utc must be timezone-aware")
        return self


class BookingOut(BaseModel):
    id: int
    user_id: int
    start_at_utc: datetime
    end_at_utc: datetime
    status: BookingStatus
    created_at: datetime
    cancelled_at: datetime | None = None
    cancelled_by_user_id: int | None = None


class BookingListResponse(BaseModel):
    items: list[BookingOut]
    total: int
    page: int
    page_size: int
