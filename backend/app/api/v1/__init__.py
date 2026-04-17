from fastapi import APIRouter

from app.api.v1 import auth, availability, bookings, health, organizations, slots

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(organizations.router, prefix="/orgs", tags=["organizations"])
api_router.include_router(availability.router, prefix="/availability", tags=["availability"])
api_router.include_router(slots.router, tags=["slots"])
api_router.include_router(bookings.router, tags=["bookings"])
