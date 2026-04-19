"""Availability CRUD — admin-only contract.

Covers:
- Org GET/PATCH (timezone NOT settable)
- Grid GET/PUT (atomic replace, overlap rejection)
- Exceptions GET/POST/PATCH/DELETE
- User role cannot mutate any of the above
- Tenant isolation: Org A admin cannot read or modify Org B
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


# ---------- helpers ----------

async def _register_admin(
    client: AsyncClient,
    *,
    slug: str,
    email: str = "admin@x.com",
    timezone: str = "UTC",
) -> None:
    r = await client.post(
        "/api/v1/auth/admin/register",
        json={
            "org_name": slug.title(),
            "org_slug": slug,
            "timezone": timezone,
            "email": email,
            "password": "secret123",
        },
    )
    assert r.status_code == 201, r.text


async def _login(client: AsyncClient, *, slug: str, email: str) -> str:
    r = await client.post(
        "/api/v1/auth/login",
        json={"org_slug": slug, "email": email, "password": "secret123"},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


async def _admin_token(client: AsyncClient, *, slug: str = "acme") -> dict[str, str]:
    await _register_admin(client, slug=slug, email=f"admin@{slug}.com")
    token = await _login(client, slug=slug, email=f"admin@{slug}.com")
    return {"Authorization": f"Bearer {token}"}


async def _user_token(client: AsyncClient, *, slug: str) -> dict[str, str]:
    body = {"org_slug": slug, "email": f"user@{slug}.com", "password": "secret123"}
    r = await client.post("/api/v1/auth/register", json=body)
    assert r.status_code == 201, r.text
    token = await _login(client, slug=slug, email=f"user@{slug}.com")
    return {"Authorization": f"Bearer {token}"}


# ---------- /orgs/me ----------

async def test_get_my_org(client: AsyncClient) -> None:
    headers = await _admin_token(client)
    r = await client.get("/api/v1/orgs/me", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "acme"
    assert body["timezone"] == "UTC"
    assert body["slot_duration_minutes"] == 30


async def test_patch_my_org(client: AsyncClient) -> None:
    headers = await _admin_token(client)
    r = await client.patch(
        "/api/v1/orgs/me",
        headers=headers,
        json={"name": "Acme Inc.", "slot_duration_minutes": 45, "min_lead_minutes": 60},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Acme Inc."
    assert body["slot_duration_minutes"] == 45
    assert body["min_lead_minutes"] == 60


async def test_patch_org_rejects_timezone_field(client: AsyncClient) -> None:
    """Pydantic strips unknown fields by default; we just confirm TZ is unchanged."""
    headers = await _admin_token(client)
    r = await client.patch(
        "/api/v1/orgs/me",
        headers=headers,
        json={"timezone": "America/New_York", "name": "Renamed"},
    )
    assert r.status_code == 200
    assert r.json()["timezone"] == "UTC"
    assert r.json()["name"] == "Renamed"


async def test_user_cannot_patch_org(client: AsyncClient) -> None:
    await _register_admin(client, slug="acme")
    user_headers = await _user_token(client, slug="acme")
    r = await client.patch(
        "/api/v1/orgs/me", headers=user_headers, json={"name": "Hacked"}
    )
    assert r.status_code == 403


# ---------- grid ----------

async def test_grid_starts_empty(client: AsyncClient) -> None:
    headers = await _admin_token(client)
    r = await client.get("/api/v1/availability/grid", headers=headers)
    assert r.status_code == 200
    assert r.json() == {"rules": []}


async def test_put_grid_replaces_all_rules(client: AsyncClient) -> None:
    headers = await _admin_token(client)
    body = {
        "rules": [
            {"day_of_week": 0, "start_local_time": "09:00:00", "end_local_time": "13:00:00"},
            {"day_of_week": 0, "start_local_time": "14:00:00", "end_local_time": "18:00:00"},
            {"day_of_week": 4, "start_local_time": "10:00:00", "end_local_time": "16:00:00"},
        ]
    }
    r = await client.put("/api/v1/availability/grid", headers=headers, json=body)
    assert r.status_code == 200
    assert len(r.json()["rules"]) == 3

    # PUT again with one rule → full replace.
    r2 = await client.put(
        "/api/v1/availability/grid",
        headers=headers,
        json={"rules": [{"day_of_week": 2, "start_local_time": "08:00:00", "end_local_time": "12:00:00"}]},
    )
    assert r2.status_code == 200
    assert len(r2.json()["rules"]) == 1
    assert r2.json()["rules"][0]["day_of_week"] == 2


async def test_put_grid_rejects_overlapping_same_day(client: AsyncClient) -> None:
    headers = await _admin_token(client)
    body = {
        "rules": [
            {"day_of_week": 0, "start_local_time": "09:00:00", "end_local_time": "13:00:00"},
            {"day_of_week": 0, "start_local_time": "12:00:00", "end_local_time": "18:00:00"},
        ]
    }
    r = await client.put("/api/v1/availability/grid", headers=headers, json=body)
    assert r.status_code == 422


async def test_put_grid_rejects_inverted_window(client: AsyncClient) -> None:
    headers = await _admin_token(client)
    body = {
        "rules": [
            {"day_of_week": 0, "start_local_time": "18:00:00", "end_local_time": "09:00:00"},
        ]
    }
    r = await client.put("/api/v1/availability/grid", headers=headers, json=body)
    assert r.status_code == 422


async def test_user_cannot_put_grid(client: AsyncClient) -> None:
    await _register_admin(client, slug="acme")
    user_headers = await _user_token(client, slug="acme")
    r = await client.put(
        "/api/v1/availability/grid",
        headers=user_headers,
        json={"rules": []},
    )
    assert r.status_code == 403


# ---------- exceptions ----------

def _exc_body(start: datetime, end: datetime, *, kind: str = "range") -> dict:
    return {
        "start_at_utc": start.isoformat(),
        "end_at_utc": end.isoformat(),
        "kind": kind,
        "reason": "holiday",
    }


async def test_exceptions_full_lifecycle(client: AsyncClient) -> None:
    headers = await _admin_token(client)
    start = datetime.now(UTC) + timedelta(days=10)
    end = start + timedelta(hours=4)

    r_create = await client.post(
        "/api/v1/availability/exceptions", headers=headers, json=_exc_body(start, end)
    )
    assert r_create.status_code == 201
    exc_id = r_create.json()["id"]

    r_get = await client.get(f"/api/v1/availability/exceptions/{exc_id}", headers=headers)
    assert r_get.status_code == 200

    r_list = await client.get("/api/v1/availability/exceptions", headers=headers)
    assert r_list.status_code == 200
    assert len(r_list.json()["items"]) == 1

    new_end = end + timedelta(hours=1)
    r_patch = await client.patch(
        f"/api/v1/availability/exceptions/{exc_id}",
        headers=headers,
        json={"end_at_utc": new_end.isoformat(), "reason": "extended"},
    )
    assert r_patch.status_code == 200
    assert r_patch.json()["reason"] == "extended"

    r_del = await client.delete(
        f"/api/v1/availability/exceptions/{exc_id}", headers=headers
    )
    assert r_del.status_code == 204

    r_get2 = await client.get(f"/api/v1/availability/exceptions/{exc_id}", headers=headers)
    assert r_get2.status_code == 404


async def test_exception_rejects_inverted_range(client: AsyncClient) -> None:
    headers = await _admin_token(client)
    start = datetime.now(UTC) + timedelta(days=1)
    end = start - timedelta(hours=1)
    r = await client.post(
        "/api/v1/availability/exceptions", headers=headers, json=_exc_body(start, end)
    )
    assert r.status_code == 422


async def test_exception_list_filters_by_range(client: AsyncClient) -> None:
    headers = await _admin_token(client)
    base = datetime.now(UTC) + timedelta(days=30)

    for i in range(3):
        s = base + timedelta(days=i)
        await client.post(
            "/api/v1/availability/exceptions",
            headers=headers,
            json=_exc_body(s, s + timedelta(hours=2)),
        )

    # Filter to just the middle day.
    f = base + timedelta(days=1)
    t = base + timedelta(days=2)
    r = await client.get(
        f"/api/v1/availability/exceptions?from={f.isoformat()}&to={t.isoformat()}",
        headers=headers,
    )
    assert r.status_code == 200
    assert len(r.json()["items"]) == 1


async def test_user_cannot_create_exception(client: AsyncClient) -> None:
    await _register_admin(client, slug="acme")
    user_headers = await _user_token(client, slug="acme")
    start = datetime.now(UTC) + timedelta(days=1)
    r = await client.post(
        "/api/v1/availability/exceptions",
        headers=user_headers,
        json=_exc_body(start, start + timedelta(hours=2)),
    )
    assert r.status_code == 403


# ---------- tenant isolation on availability ----------

async def test_org_a_cannot_see_org_b_exception(client: AsyncClient) -> None:
    headers_a = await _admin_token(client, slug="alpha")
    headers_b = await _admin_token(client, slug="beta")

    start = datetime.now(UTC) + timedelta(days=5)
    r = await client.post(
        "/api/v1/availability/exceptions",
        headers=headers_b,
        json=_exc_body(start, start + timedelta(hours=2)),
    )
    assert r.status_code == 201
    exc_id_b = r.json()["id"]

    # Org A admin tries to GET / PATCH / DELETE Org B's exception → 404 (not 403, no leak).
    r_get = await client.get(
        f"/api/v1/availability/exceptions/{exc_id_b}", headers=headers_a
    )
    assert r_get.status_code == 404

    r_patch = await client.patch(
        f"/api/v1/availability/exceptions/{exc_id_b}",
        headers=headers_a,
        json={"reason": "evil"},
    )
    assert r_patch.status_code == 404

    r_del = await client.delete(
        f"/api/v1/availability/exceptions/{exc_id_b}", headers=headers_a
    )
    assert r_del.status_code == 404

    # Org A's list does not include Org B's exception.
    r_list = await client.get("/api/v1/availability/exceptions", headers=headers_a)
    assert r_list.status_code == 200
    assert r_list.json()["items"] == []
