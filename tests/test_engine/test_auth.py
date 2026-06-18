from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from megatron.web.app import app

    return TestClient(app)


def test_hash_and_verify_password():
    from megatron.core.security import hash_password, verify_password

    h = hash_password("secret")
    assert h != "secret"
    assert verify_password("secret", h) is True
    assert verify_password("wrong", h) is False


def test_unauthenticated_dashboard_rejected(client):
    r = client.get("/ui/dashboard", follow_redirects=False)
    # Redirect to login (303) now, not 401
    assert r.status_code in (303, 401)


def test_unauthenticated_api_rejected(client):
    r = client.get("/api/admin/modules")
    assert r.status_code == 401


def test_login_page_renders(client):
    r = client.get("/ui/login")
    assert r.status_code == 200
    assert "Username" in r.text
    assert "Password" in r.text


def test_login_wrong_password(client):
    from megatron.core.db import async_session_factory
    from megatron.core.engine_models import User
    from megatron.core.security import hash_password

    import asyncio

    async def seed():
        async with async_session_factory() as s:
            s.add(
                User(
                    username="admin",
                    password_hash=hash_password("admin"),
                    display_name="A",
                    is_active=True,
                )
            )
            await s.commit()

    asyncio.run(seed())

    r = client.post(
        "/ui/login", data={"username": "admin", "password": "wrong"}, follow_redirects=False
    )
    assert r.status_code == 303
    assert "error=1" in r.headers["location"]


def test_login_correct_then_access(client):
    from megatron.core.db import async_session_factory
    from megatron.core.engine_models import User
    from megatron.core.security import hash_password

    import asyncio

    async def seed():
        async with async_session_factory() as s:
            s.add(
                User(
                    username="admin",
                    password_hash=hash_password("admin"),
                    display_name="A",
                    is_active=True,
                )
            )
            await s.commit()

    asyncio.run(seed())

    r = client.post(
        "/ui/login", data={"username": "admin", "password": "admin"}, follow_redirects=False
    )
    assert r.status_code == 303
    assert "/ui/dashboard" in r.headers["location"]

    r2 = client.get("/api/admin/modules")
    assert r2.status_code == 200


def test_logout_clears_session(client):
    from megatron.core.db import async_session_factory
    from megatron.core.engine_models import User
    from megatron.core.security import hash_password

    import asyncio

    async def seed():
        async with async_session_factory() as s:
            s.add(
                User(
                    username="admin",
                    password_hash=hash_password("admin"),
                    display_name="A",
                    is_active=True,
                )
            )
            await s.commit()

    asyncio.run(seed())
    client.post(
        "/ui/login", data={"username": "admin", "password": "admin"}, follow_redirects=False
    )

    r = client.get("/ui/logout", follow_redirects=False)
    assert r.status_code == 303

    r2 = client.get("/api/admin/modules")
    assert r2.status_code == 401
