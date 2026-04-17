"""Aggregate model imports so Alembic autogenerate sees every table."""

from app.models.availability import AvailabilityRule, Exception_, ExceptionKind
from app.models.booking import Booking, BookingStatus
from app.models.organization import Organization
from app.models.refresh_token import RefreshToken
from app.models.user import User, UserRole

__all__ = [
    "AvailabilityRule",
    "Booking",
    "BookingStatus",
    "Exception_",
    "ExceptionKind",
    "Organization",
    "RefreshToken",
    "User",
    "UserRole",
]
