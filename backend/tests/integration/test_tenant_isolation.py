"""Tenant-isolation guarantees that don't depend on availability/booking endpoints.

Once Phases 5 and 6 land, this module gets new cases (Org A admin can't read
Org B grid/exceptions/bookings, etc). For now we lock in the boundaries that
already exist:

- email uniqueness is per-org, not global
- a user logged into Org A cannot impersonate the *same email* in Org B
- /me always reflects the org claim the JWT was minted for
- the access token from Org A is structurally bound to Org A's org_id (claim)
"""
from __future__ import annotations

import jwt
import pytest
from httpx import AsyncClient

from app.core.config import get_settings

pytestmark = pytest.mark.asyncio


async def _register_admin(
    client: AsyncClient,
    *,
    org_slug: str,
    org_name: str | None = None,
    email: str = "admin@x.com",
    password: str = "secret123",
    timezone: str = "UTC",
) -> dict:
    r = await client.post(
        "/api/v1/auth/admin/register",
        json={
            "org_name": org_name or org_slug.title(),
            "org_slug": org_slug,
            "timezone": timezone,
            "email": email,
            "password": password,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _login(
    client: AsyncClient, *, org_slug: str, email: str, password: str = "secret123"
) -> dict:
    r = await client.post(
        "/api/v1/auth/login",
        json={"org_slug": org_slug, "email": email, "password": password},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def test_same_email_can_exist_in_two_orgs(client: AsyncClient) -> None:
    """Email uniqueness is scoped to organization_id, not global."""
    await _register_admin(client, org_slug="alpha", email="shared@x.com")
    await _register_admin(client, org_slug="beta", email="shared@x.com")

    a = await _login(client, org_slug="alpha", email="shared@x.com")
    b = await _login(client, org_slug="beta", email="shared@x.com")

    # Different access tokens mean different identities even with the same email.
    assert a["access_token"] != b["access_token"]


async def test_login_with_wrong_org_for_email_fails(client: AsyncClient) -> None:
    """Email exists in Alpha; logging into Beta with that email is 401, not a leak."""
    await _register_admin(client, org_slug="alpha", email="only@alpha.com")
    await _register_admin(client, org_slug="beta", email="someone@beta.com")

    r = await client.post(
        "/api/v1/auth/login",
        json={"org_slug": "beta", "email": "only@alpha.com", "password": "secret123"},
    )
    assert r.status_code == 401


async def test_me_returns_correct_org_per_token(client: AsyncClient) -> None:
    """Two parallel sessions from different orgs each see only their own org."""
    await _register_admin(client, org_slug="alpha", email="a@a.com")
    await _register_admin(client, org_slug="beta", email="b@b.com")

    a = await _login(client, org_slug="alpha", email="a@a.com")
    b = await _login(client, org_slug="beta", email="b@b.com")

    me_a = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {a['access_token']}"}
    )
    me_b = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {b['access_token']}"}
    )

    assert me_a.status_code == 200 and me_b.status_code == 200
    assert me_a.json()["organization_slug"] == "alpha"
    assert me_b.json()["organization_slug"] == "beta"
    assert me_a.json()["organization_id"] != me_b.json()["organization_id"]


async def test_access_token_carries_org_claim(client: AsyncClient) -> None:
    """Defense-in-depth: every downstream service relies on org_id being in the JWT.

    If this claim ever stops being emitted, the entire tenant-scoping model collapses.
    """
    await _register_admin(client, org_slug="alpha", email="a@a.com")
    body = await _login(client, org_slug="alpha", email="a@a.com")

    settings = get_settings()
    claims = jwt.decode(
        body["access_token"],
        settings.jwt_public_key_pem(),
        algorithms=[settings.jwt_algorithm],
        issuer=settings.jwt_issuer,
    )

    assert "org_id" in claims and isinstance(claims["org_id"], int)
    assert claims["role"] == "admin"
    assert int(claims["sub"]) > 0


async def test_refresh_tokens_are_independent_per_org(client: AsyncClient) -> None:
    """A leaked Alpha refresh must never produce a Beta access token."""
    await _register_admin(client, org_slug="alpha", email="a@a.com")
    await _register_admin(client, org_slug="beta", email="b@b.com")

    a = await _login(client, org_slug="alpha", email="a@a.com")
    b = await _login(client, org_slug="beta", email="b@b.com")

    rotated_a = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": a["refresh_token"]}
    )
    assert rotated_a.status_code == 200

    settings = get_settings()
    new_a_claims = jwt.decode(
        rotated_a.json()["access_token"],
        settings.jwt_public_key_pem(),
        algorithms=[settings.jwt_algorithm],
        issuer=settings.jwt_issuer,
    )
    b_claims = jwt.decode(
        b["access_token"],
        settings.jwt_public_key_pem(),
        algorithms=[settings.jwt_algorithm],
        issuer=settings.jwt_issuer,
    )
    assert new_a_claims["org_id"] != b_claims["org_id"]
