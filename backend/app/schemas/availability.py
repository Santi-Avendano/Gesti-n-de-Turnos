from __future__ import annotations

from datetime import datetime, time

from pydantic import BaseModel, Field, model_validator

from app.models import ExceptionKind


class GridRuleIn(BaseModel):
    day_of_week: int = Field(ge=0, le=6)
    start_local_time: time
    end_local_time: time

    @model_validator(mode="after")
    def _end_after_start(self) -> "GridRuleIn":
        if self.end_local_time <= self.start_local_time:
            raise ValueError("end_local_time must be after start_local_time")
        return self


class GridRuleOut(GridRuleIn):
    id: int


class GridReplaceRequest(BaseModel):
    rules: list[GridRuleIn]


class GridResponse(BaseModel):
    rules: list[GridRuleOut]


class ExceptionIn(BaseModel):
    start_at_utc: datetime
    end_at_utc: datetime
    kind: ExceptionKind
    reason: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def _validate(self) -> "ExceptionIn":
        if self.start_at_utc.tzinfo is None or self.end_at_utc.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        if self.end_at_utc <= self.start_at_utc:
            raise ValueError("end_at_utc must be after start_at_utc")
        return self


class ExceptionPatch(BaseModel):
    start_at_utc: datetime | None = None
    end_at_utc: datetime | None = None
    kind: ExceptionKind | None = None
    reason: str | None = Field(default=None, max_length=255)


class ExceptionOut(BaseModel):
    id: int
    start_at_utc: datetime
    end_at_utc: datetime
    kind: ExceptionKind
    reason: str | None = None


class ExceptionListResponse(BaseModel):
    items: list[ExceptionOut]
