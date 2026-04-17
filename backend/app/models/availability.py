from __future__ import annotations

from datetime import datetime, time
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Time,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ExceptionKind(StrEnum):
    FULL_DAY = "full_day"
    RANGE = "range"


class AvailabilityRule(Base):
    __tablename__ = "availability_rules"
    __table_args__ = (
        CheckConstraint("day_of_week BETWEEN 0 AND 6", name="dow_range"),
        CheckConstraint("end_local_time > start_local_time", name="end_after_start"),
        Index("ix_availability_rules_organization_id_day_of_week", "organization_id", "day_of_week"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    day_of_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    start_local_time: Mapped[time] = mapped_column(Time(timezone=False), nullable=False)
    end_local_time: Mapped[time] = mapped_column(Time(timezone=False), nullable=False)


class Exception_(Base):
    """Renamed in code to avoid shadowing builtin `Exception`. Table is `exceptions`."""

    __tablename__ = "exceptions"
    __table_args__ = (
        CheckConstraint("end_at_utc > start_at_utc", name="end_after_start"),
        Index(
            "ix_exceptions_organization_id_start_end",
            "organization_id",
            "start_at_utc",
            "end_at_utc",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    start_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    kind: Mapped[ExceptionKind] = mapped_column(
        Enum(ExceptionKind, name="exception_kind", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
