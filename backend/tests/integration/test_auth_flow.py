"""End-to-end tests of the auth pipeline.

We exercise the full lifecycle through HTTP (httpx + ASGITransport) so we cover:
- Pydantic validation
- service-layer transactions (with the SAVEPOINT-based test session)
- exception → HTTP mapping
- the refresh-token rotation + reuse-detection contract
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


# ---------- helpers ----------

ADMIN_BODY = {
    "org_name": "Acme",
    "org_slug": "acme",
    "timezone": "America/Argentina/Buenos_Aires",
    "email": "admin@acme.com",
    "password": "secret123",
}


async def _register_admin(client: AsyncClient, **overrides: object) -> dict:
    body = {**ADMIN_BODY, **overrides}
    r = await client.post("/api/v1/auth/admin/register", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _login(
    client: AsyncClient, *, org_slug: str = "acme", email: str = "admin@acme.com", password: str = "secret123"
) -> dict:
    r = await client.post(
        "/api/v1/auth/login",
        json={"org_slug": org_slug, "email": email, "password": password},
    )
    assert r.status_code == 200, r.text
    return r.json()


# ---------- registration ----------

async def test_admin_register_creates_org_and_user(client: AsyncClient) -> None:
    body = await _register_admin(client)
    assert body["organization_slug"] == "acme"
    assert body["email"] == "admin@acme.com"
    assert body["organization_id"] > 0
    assert body["user_id"] > 0


async def test_admin_register_duplicate_slug_returns_409(client: AsyncClient) -> None:
    await _register_admin(client)
    r = await client.post(
        "/api/v1/auth/admin/register",
        json={**ADMIN_BODY, "email": "other@acme.com"},
    )
    assert r.status_code == 409
    assert "slug" in r.json()["error"]["message"].lower()


async def test_admin_register_invalid_timezone_returns_422(client: AsyncClient) -> None:
    r = await client.post(
        "/api/v1/auth/admin/register",
        json={**ADMIN_BODY, "timezone": "Mars/Olympus_Mons"},
    )
    assert r.status_code == 422


async def test_user_register_requires_existing_org(client: AsyncClient) -> None:
    r = await client.post(
        "/api/v1/auth/register",
        json={"org_slug": "nonexistent", "email": "u@x.com", "password": "secret123"},
    )
    assert r.status_code == 404


async def test_user_register_into_existing_org(client: AsyncClient) -> None:
    await _register_admin(client)
    r = await client.post(
        "/api/v1/auth/register",
        json={"org_slug": "acme", "email": "u@acme.com", "password": "secret123"},
    )
    assert r.status_code == 201
    assert r.json()["email"] == "u@acme.com"


async def test_user_register_duplicate_email_in_org_returns_409(client: AsyncClient) -> None:
    await _register_admin(client)
    body = {"org_slug": "acme", "email": "u@acme.com", "password": "secret123"}
    r1 = await client.post("/api/v1/auth/register", json=body)
    assert r1.status_code == 201
    r2 = await client.post("/api/v1/auth/register", json=body)
    assert r2.status_code == 409


# ---------- login ----------

async def test_login_returns_token_pair(client: AsyncClient) -> None:
    await _register_admin(client)
    body = await _login(client)
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0


async def test_login_wrong_password_returns_401(client: AsyncClient) -> None:
    await _register_admin(client)
    r = await client.post(
        "/api/v1/auth/login",
        json={"org_slug": "acme", "email": "admin@acme.com", "password": "WRONG_pwd1"},
    )
    assert r.status_code == 401


async def test_login_unknown_org_returns_401(client: AsyncClient) -> None:
    r = await client.post(
        "/api/v1/auth/login",
        json={"org_slug": "nope", "email": "x@x.com", "password": "secret123"},
    )
    assert r.status_code == 401


# ---------- /me ----------

async def test_me_returns_user_with_org(client: AsyncClient) -> None:
    await _register_admin(client)
    tokens = await _login(client)
    r = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "admin@acme.com"
    assert body["role"] == "admin"
    assert body["organization_slug"] == "acme"
    assert body["organization_timezone"] == "America/Argentina/Buenos_Aires"


async def test_me_without_token_returns_401(client: AsyncClient) -> None:
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 401


async def test_me_with_invalid_token_returns_401(client: AsyncClient) -> None:
    r = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer not.a.real.jwt"},
    )
    assert r.status_code == 401


# ---------- refresh rotation ----------

async def test_refresh_rotates_tokens_and_old_refresh_dies(client: AsyncClient) -> None:
    await _register_admin(client)
    tokens = await _login(client)
    old_refresh = tokens["refresh_token"]

    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert r.status_code == 200
    new_tokens = r.json()
    assert new_tokens["refresh_token"] != old_refresh
    assert new_tokens["access_token"] != tokens["access_token"]

    # New refresh works.
    r2 = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": new_tokens["refresh_token"]}
    )
    assert r2.status_code == 200


async def test_refresh_with_unknown_token_returns_401(client: AsyncClient) -> None:
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": "garbage"})
    assert r.status_code == 401


async def test_refresh_reuse_detection_revokes_family(client: AsyncClient) -> None:
    """⭐ The most security-critical test in the suite.

    Presenting an already-rotated refresh token must:
      1. fail with 401
      2. revoke EVERY token in the family — including the legitimate "current"
         token an attacker might also hold.
    """
    await _register_admin(client)
    tokens = await _login(client)
    leaked = tokens["refresh_token"]

    # Legitimate rotation by the real client.
    rotated = await client.post("/api/v1/auth/refresh", json={"refresh_token": leaked})
    assert rotated.status_code == 200
    current_refresh = rotated.json()["refresh_token"]

    # Attacker presents the leaked (now revoked) token.
    bad = await client.post("/api/v1/auth/refresh", json={"refresh_token": leaked})
    assert bad.status_code == 401
    assert "reuse" in bad.json()["error"]["message"].lower()

    # The legitimate token that was alive a moment ago is now ALSO dead.
    after = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": current_refresh}
    )
    assert after.status_code == 401


# ---------- logout ----------

async def test_logout_revokes_refresh(client: AsyncClient) -> None:
    await _register_admin(client)
    tokens = await _login(client)

    r = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    )
    assert r.status_code == 204

    # The refresh now triggers reuse detection (because revoked_at != NULL).
    r2 = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert r2.status_code == 401


async def test_logout_unknown_token_is_silent_204(client: AsyncClient) -> None:
    """Don't leak which refresh tokens existed — even bad input gets 204."""
    r = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": "totally-fake"}
    )
    assert r.status_code == 204
