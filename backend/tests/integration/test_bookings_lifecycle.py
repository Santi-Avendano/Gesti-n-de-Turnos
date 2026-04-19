"""Bookings + slots end-to-end lifecycle.

Flow:
  1. admin sets a 7-day-a-week 09:00–18:00 grid (UTC) so any near-future weekday is bookable
  2. user lists slots → at least one
  3. user books a slot → 201
  4. that same slot disappears from /slots
  5. user cancels → slot reappears
  6. admin reschedules a booking
  7. validation: out-of-grid / past / beyond-horizon / unaligned slots are rejected
  8. tenant isolation: org A user can't read org B's booking
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


# ---------- helpers ----------

async def _register_admin(client: AsyncClient, *, slug: str) -> None:
    r = await client.post(
        "/api/v1/auth/admin/register",
        json={
            "org_name": slug.title(),
            "org_slug": slug,
            "timezone": "UTC",
            "email": f"admin@{slug}.com",
            "password": "secret123",
        },
    )
    assert r.status_code == 201, r.text


async def _register_user(client: AsyncClient, *, slug: str, email: str) -> None:
    r = await client.post(
        "/api/v1/auth/register",
        json={"org_slug": slug, "email": email, "password": "secret123"},
    )
    assert r.status_code == 201, r.text


async def _token(client: AsyncClient, *, slug: str, email: str) -> str:
    r = await client.post(
        "/api/v1/auth/login",
        json={"org_slug": slug, "email": email, "password": "secret123"},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _setup_open_grid(client: AsyncClient, *, admin_token: str) -> None:
    """Grid: every day of the week 09:00–18:00 UTC."""
    rules = [
        {"day_of_week": d, "start_local_time": "09:00:00", "end_local_time": "18:00:00"}
        for d in range(7)
    ]
    r = await client.put(
        "/api/v1/availability/grid", headers=_hdr(admin_token), json={"rules": rules}
    )
    assert r.status_code == 200, r.text


def _next_bookable_slot_utc() -> datetime:
    """Pick tomorrow 09:00 UTC — guaranteed inside the open grid + after lead 0."""
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).date()
    return datetime.combine(tomorrow, datetime.min.time(), tzinfo=UTC).replace(hour=9)


# ---------- slots query ----------

async def test_slots_returns_items_after_grid_setup(client: AsyncClient) -> None:
    await _register_admin(client, slug="acme")
    admin_token = await _token(client, slug="acme", email="admin@acme.com")
    await _setup_open_grid(client, admin_token=admin_token)

    today = date.today()
    end = today + timedelta(days=2)
    r = await client.get(
        f"/api/v1/slots?from={today.isoformat()}&to={end.isoformat()}",
        headers=_hdr(admin_token),
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) > 0


async def test_slots_rejects_range_beyond_horizon(client: AsyncClient) -> None:
    await _register_admin(client, slug="acme")
    admin_token = await _token(client, slug="acme", email="admin@acme.com")

    # default horizon is 60 days
    today = date.today()
    end = today + timedelta(days=120)
    r = await client.get(
        f"/api/v1/slots?from={today.isoformat()}&to={end.isoformat()}",
        headers=_hdr(admin_token),
    )
    assert r.status_code == 422


# ---------- create + lifecycle ----------

async def test_user_can_book_and_then_slot_disappears(client: AsyncClient) -> None:
    await _register_admin(client, slug="acme")
    admin_token = await _token(client, slug="acme", email="admin@acme.com")
    await _setup_open_grid(client, admin_token=admin_token)
    await _register_user(client, slug="acme", email="u@acme.com")
    user_token = await _token(client, slug="acme", email="u@acme.com")

    slot = _next_bookable_slot_utc()

    r_create = await client.post(
        "/api/v1/bookings",
        headers=_hdr(user_token),
        json={"slot_start_at_utc": slot.isoformat()},
    )
    assert r_create.status_code == 201, r_create.text

    # The same slot now disappears from the available list.
    r_slots = await client.get(
        f"/api/v1/slots?from={slot.date().isoformat()}&to={slot.date().isoformat()}",
        headers=_hdr(user_token),
    )
    assert r_slots.status_code == 200
    available_starts = {item["start_at_utc"] for item in r_slots.json()["items"]}
    assert slot.isoformat().replace("+00:00", "Z") not in available_starts
    # Compare normalized: pydantic serializes Z-or-+00:00, so check by parsing
    parsed = {datetime.fromisoformat(s.replace("Z", "+00:00")) for s in available_starts}
    assert slot not in parsed


async def test_double_booking_returns_409(client: AsyncClient) -> None:
    await _register_admin(client, slug="acme")
    admin_token = await _token(client, slug="acme", email="admin@acme.com")
    await _setup_open_grid(client, admin_token=admin_token)
    await _register_user(client, slug="acme", email="u@acme.com")
    user_token = await _token(client, slug="acme", email="u@acme.com")

    slot = _next_bookable_slot_utc()
    body = {"slot_start_at_utc": slot.isoformat()}

    r1 = await client.post("/api/v1/bookings", headers=_hdr(user_token), json=body)
    assert r1.status_code == 201
    r2 = await client.post("/api/v1/bookings", headers=_hdr(user_token), json=body)
    assert r2.status_code == 409


async def test_user_cancels_own_booking_and_slot_reappears(client: AsyncClient) -> None:
    await _register_admin(client, slug="acme")
    admin_token = await _token(client, slug="acme", email="admin@acme.com")
    await _setup_open_grid(client, admin_token=admin_token)
    await _register_user(client, slug="acme", email="u@acme.com")
    user_token = await _token(client, slug="acme", email="u@acme.com")

    slot = _next_bookable_slot_utc()
    r = await client.post(
        "/api/v1/bookings", headers=_hdr(user_token), json={"slot_start_at_utc": slot.isoformat()}
    )
    booking_id = r.json()["id"]

    r_del = await client.delete(f"/api/v1/bookings/{booking_id}", headers=_hdr(user_token))
    assert r_del.status_code == 204

    # Booking is now bookable again.
    r2 = await client.post(
        "/api/v1/bookings", headers=_hdr(user_token), json={"slot_start_at_utc": slot.isoformat()}
    )
    assert r2.status_code == 201


async def test_user_cannot_cancel_other_users_booking(client: AsyncClient) -> None:
    await _register_admin(client, slug="acme")
    admin_token = await _token(client, slug="acme", email="admin@acme.com")
    await _setup_open_grid(client, admin_token=admin_token)
    await _register_user(client, slug="acme", email="alice@acme.com")
    await _register_user(client, slug="acme", email="bob@acme.com")
    alice = await _token(client, slug="acme", email="alice@acme.com")
    bob = await _token(client, slug="acme", email="bob@acme.com")

    slot = _next_bookable_slot_utc()
    r = await client.post(
        "/api/v1/bookings", headers=_hdr(alice), json={"slot_start_at_utc": slot.isoformat()}
    )
    booking_id = r.json()["id"]

    # Bob trying to read or cancel Alice's booking is treated as 404.
    r_get = await client.get(f"/api/v1/bookings/{booking_id}", headers=_hdr(bob))
    assert r_get.status_code == 404

    r_del = await client.delete(f"/api/v1/bookings/{booking_id}", headers=_hdr(bob))
    assert r_del.status_code == 404


async def test_admin_can_reschedule_user_booking(client: AsyncClient) -> None:
    await _register_admin(client, slug="acme")
    admin_token = await _token(client, slug="acme", email="admin@acme.com")
    await _setup_open_grid(client, admin_token=admin_token)
    await _register_user(client, slug="acme", email="u@acme.com")
    user_token = await _token(client, slug="acme", email="u@acme.com")

    slot = _next_bookable_slot_utc()
    r = await client.post(
        "/api/v1/bookings", headers=_hdr(user_token), json={"slot_start_at_utc": slot.isoformat()}
    )
    booking_id = r.json()["id"]

    new_slot = slot + timedelta(hours=1)
    r_patch = await client.patch(
        f"/api/v1/bookings/{booking_id}",
        headers=_hdr(admin_token),
        json={"new_slot_start_at_utc": new_slot.isoformat()},
    )
    assert r_patch.status_code == 200
    new_id = r_patch.json()["id"]
    assert new_id != booking_id  # reschedule = cancel + create
    assert r_patch.json()["status"] == "active"


# ---------- validation ----------

async def test_booking_in_past_is_rejected(client: AsyncClient) -> None:
    await _register_admin(client, slug="acme")
    admin_token = await _token(client, slug="acme", email="admin@acme.com")
    await _setup_open_grid(client, admin_token=admin_token)
    await _register_user(client, slug="acme", email="u@acme.com")
    user_token = await _token(client, slug="acme", email="u@acme.com")

    past = datetime.now(UTC) - timedelta(days=1)
    past = past.replace(hour=10, minute=0, second=0, microsecond=0)
    r = await client.post(
        "/api/v1/bookings",
        headers=_hdr(user_token),
        json={"slot_start_at_utc": past.isoformat()},
    )
    assert r.status_code == 422


async def test_booking_outside_grid_is_rejected(client: AsyncClient) -> None:
    await _register_admin(client, slug="acme")
    admin_token = await _token(client, slug="acme", email="admin@acme.com")
    await _setup_open_grid(client, admin_token=admin_token)
    await _register_user(client, slug="acme", email="u@acme.com")
    user_token = await _token(client, slug="acme", email="u@acme.com")

    # 03:00 UTC tomorrow is outside the 09–18 grid.
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).date()
    out_of_grid = datetime.combine(tomorrow, datetime.min.time(), tzinfo=UTC).replace(hour=3)
    r = await client.post(
        "/api/v1/bookings",
        headers=_hdr(user_token),
        json={"slot_start_at_utc": out_of_grid.isoformat()},
    )
    assert r.status_code == 422


async def test_booking_unaligned_slot_is_rejected(client: AsyncClient) -> None:
    await _register_admin(client, slug="acme")
    admin_token = await _token(client, slug="acme", email="admin@acme.com")
    await _setup_open_grid(client, admin_token=admin_token)
    await _register_user(client, slug="acme", email="u@acme.com")
    user_token = await _token(client, slug="acme", email="u@acme.com")

    # 09:15 with 30-min slots starting at 09:00 → not aligned.
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).date()
    misaligned = datetime.combine(tomorrow, datetime.min.time(), tzinfo=UTC).replace(
        hour=9, minute=15
    )
    r = await client.post(
        "/api/v1/bookings",
        headers=_hdr(user_token),
        json={"slot_start_at_utc": misaligned.isoformat()},
    )
    assert r.status_code == 422


async def test_booking_beyond_horizon_is_rejected(client: AsyncClient) -> None:
    await _register_admin(client, slug="acme")
    admin_token = await _token(client, slug="acme", email="admin@acme.com")
    await _setup_open_grid(client, admin_token=admin_token)
    await _register_user(client, slug="acme", email="u@acme.com")
    user_token = await _token(client, slug="acme", email="u@acme.com")

    far = datetime.now(UTC) + timedelta(days=400)
    far = far.replace(hour=9, minute=0, second=0, microsecond=0)
    r = await client.post(
        "/api/v1/bookings",
        headers=_hdr(user_token),
        json={"slot_start_at_utc": far.isoformat()},
    )
    assert r.status_code == 422


# ---------- list ----------

async def test_list_my_bookings_pagination(client: AsyncClient) -> None:
    await _register_admin(client, slug="acme")
    admin_token = await _token(client, slug="acme", email="admin@acme.com")
    await _setup_open_grid(client, admin_token=admin_token)
    await _register_user(client, slug="acme", email="u@acme.com")
    user_token = await _token(client, slug="acme", email="u@acme.com")

    base = _next_bookable_slot_utc()
    for i in range(3):
        slot = base + timedelta(hours=i)
        r = await client.post(
            "/api/v1/bookings",
            headers=_hdr(user_token),
            json={"slot_start_at_utc": slot.isoformat()},
        )
        assert r.status_code == 201, r.text

    r = await client.get(
        "/api/v1/bookings/me?page=1&page_size=2", headers=_hdr(user_token)
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert body["page"] == 1
    assert body["page_size"] == 2


async def test_admin_can_list_org_bookings(client: AsyncClient) -> None:
    await _register_admin(client, slug="acme")
    admin_token = await _token(client, slug="acme", email="admin@acme.com")
    await _setup_open_grid(client, admin_token=admin_token)
    await _register_user(client, slug="acme", email="alice@acme.com")
    await _register_user(client, slug="acme", email="bob@acme.com")
    alice = await _token(client, slug="acme", email="alice@acme.com")
    bob = await _token(client, slug="acme", email="bob@acme.com")

    base = _next_bookable_slot_utc()
    await client.post(
        "/api/v1/bookings",
        headers=_hdr(alice),
        json={"slot_start_at_utc": base.isoformat()},
    )
    await client.post(
        "/api/v1/bookings",
        headers=_hdr(bob),
        json={"slot_start_at_utc": (base + timedelta(hours=1)).isoformat()},
    )

    r = await client.get("/api/v1/bookings", headers=_hdr(admin_token))
    assert r.status_code == 200
    assert r.json()["total"] == 2

    # User cannot list org bookings.
    r2 = await client.get("/api/v1/bookings", headers=_hdr(alice))
    assert r2.status_code == 403


# ---------- admin calendar ----------

async def test_admin_calendar_returns_free_slots_and_bookings(client: AsyncClient) -> None:
    await _register_admin(client, slug="acme")
    admin_token = await _token(client, slug="acme", email="admin@acme.com")
    await _setup_open_grid(client, admin_token=admin_token)
    await _register_user(client, slug="acme", email="u@acme.com")
    user_token = await _token(client, slug="acme", email="u@acme.com")

    slot = _next_bookable_slot_utc()
    r_book = await client.post(
        "/api/v1/bookings", headers=_hdr(user_token), json={"slot_start_at_utc": slot.isoformat()}
    )
    assert r_book.status_code == 201

    r = await client.get(
        f"/api/v1/admin/calendar?from={slot.date().isoformat()}&to={slot.date().isoformat()}",
        headers=_hdr(admin_token),
    )
    assert r.status_code == 200
    body = r.json()
    assert any(b["user_email"] == "u@acme.com" for b in body["bookings"])
    assert len(body["free_slots"]) > 0


# ---------- tenant isolation ----------

async def test_user_cannot_read_another_orgs_booking(client: AsyncClient) -> None:
    await _register_admin(client, slug="alpha")
    admin_a = await _token(client, slug="alpha", email="admin@alpha.com")
    await _setup_open_grid(client, admin_token=admin_a)
    await _register_user(client, slug="alpha", email="u@alpha.com")
    u_a = await _token(client, slug="alpha", email="u@alpha.com")

    await _register_admin(client, slug="beta")
    admin_b = await _token(client, slug="beta", email="admin@beta.com")
    await _setup_open_grid(client, admin_token=admin_b)
    await _register_user(client, slug="beta", email="u@beta.com")
    u_b = await _token(client, slug="beta", email="u@beta.com")

    slot = _next_bookable_slot_utc()
    r = await client.post(
        "/api/v1/bookings", headers=_hdr(u_a), json={"slot_start_at_utc": slot.isoformat()}
    )
    booking_id_a = r.json()["id"]

    # u_b in beta org tries to read alpha's booking → 404
    r_get = await client.get(f"/api/v1/bookings/{booking_id_a}", headers=_hdr(u_b))
    assert r_get.status_code == 404

    # admin_b tries → 404 too (not in their org)
    r_get_b = await client.get(f"/api/v1/bookings/{booking_id_a}", headers=_hdr(admin_b))
    assert r_get_b.status_code == 404
