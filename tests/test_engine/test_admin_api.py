from __future__ import annotations

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from megatron.web.app import app


@pytest_asyncio.fixture
async def admin_client():
    from megatron.config import settings

    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {settings.admin_token}"}
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as c:
        yield c


@pytest.mark.asyncio
async def test_provider_crud(admin_client):
    r = await admin_client.post(
        "/api/admin/providers",
        json={
            "name": "test-deepseek",
            "model": "deepseek/deepseek-chat",
            "api_key": "sk-test-1234567890abcdef",
            "temperature": 0.3,
            "max_tokens": 2048,
        },
    )
    assert r.status_code == 201, r.text
    provider = r.json()
    assert provider["name"] == "test-deepseek"
    assert "sk-test" not in provider["api_key_masked"]
    assert provider["model"] == "deepseek/deepseek-chat"

    r2 = await admin_client.get("/api/admin/providers")
    assert r2.status_code == 200
    assert any(p["name"] == "test-deepseek" for p in r2.json())


@pytest.mark.asyncio
async def test_provider_masked_key_never_leaks(admin_client):
    r = await admin_client.post(
        "/api/admin/providers",
        json={"name": "leak-test", "model": "openai/gpt-4o", "api_key": "sk-SUPERSECRET123456"},
    )
    body = r.json()
    assert "SUPERSECRET" not in json.dumps(body)


@pytest.mark.asyncio
async def test_admin_auth_required():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/admin/providers")
        assert r.status_code == 401
        r2 = await c.get("/api/admin/providers", headers={"Authorization": "Bearer wrong"})
        assert r2.status_code == 401


@pytest.mark.asyncio
async def test_module_create_and_options(admin_client):
    r = await admin_client.get("/api/admin/modules/options")
    assert r.status_code == 200
    opts = r.json()
    assert "twitter" in opts["sources"]
    assert "interaction" in opts["filters"]

    tmpl_r = await admin_client.post(
        "/api/admin/prompts",
        json={
            "name": "test-tmpl",
            "template": "Analyze {{ item_count }} items.",
            "output_schema": {},
        },
    )
    tmpl_id = tmpl_r.json()["id"]

    prov_r = await admin_client.post(
        "/api/admin/providers",
        json={"name": "test-prov", "model": "deepseek/deepseek-chat", "api_key": "sk-x"},
    )
    prov_id = prov_r.json()["id"]

    mod_r = await admin_client.post(
        "/api/admin/modules",
        json={
            "name": "test-module",
            "source": "twitter",
            "source_ref": "",
            "filter_config": {
                "window_hours": 24,
                "filters": [{"name": "interaction", "config": {"threshold": 0}}],
                "max_items": 10,
            },
            "prompt_template_id": tmpl_id,
            "provider_id": prov_id,
        },
    )
    assert mod_r.status_code == 201
    assert mod_r.json()["name"] == "test-module"

    r2 = await admin_client.get("/api/admin/modules")
    assert any(m["name"] == "test-module" for m in r2.json())
