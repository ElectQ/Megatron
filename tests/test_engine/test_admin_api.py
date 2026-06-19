from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

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
async def test_sqlite_foreign_keys_enabled():
    from megatron.core.db import async_session_factory

    async with async_session_factory() as session:
        value = (await session.execute(text("PRAGMA foreign_keys"))).scalar_one()
    assert value == 1


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

    channel_r = await admin_client.post(
        "/api/admin/channels",
        json={
            "name": "test-channel",
            "kind": "telegram",
            "config": {"bot_token": "fake-token", "chat_id": "123"},
        },
    )
    assert channel_r.status_code == 201, channel_r.text
    channel_id = channel_r.json()["id"]

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
            "webhook_channel_ids": [channel_id],
        },
    )
    assert mod_r.status_code == 201
    assert mod_r.json()["name"] == "test-module"
    assert mod_r.json()["webhook_channel_ids"] == [channel_id]

    from megatron.core.db import async_session_factory
    from megatron.core.engine_models import ModuleChannel

    async with async_session_factory() as session:
        link = (
            await session.execute(
                select(ModuleChannel).where(
                    ModuleChannel.module_id == mod_r.json()["id"],
                    ModuleChannel.channel_id == channel_id,
                )
            )
        ).scalar_one_or_none()
    assert link is not None

    r2 = await admin_client.get("/api/admin/modules")
    assert any(m["name"] == "test-module" for m in r2.json())
    listed = next(m for m in r2.json() if m["name"] == "test-module")
    assert listed["webhook_channel_ids"] == [channel_id]

    run_r = await admin_client.post(f"/api/admin/modules/{mod_r.json()['id']}/run")
    assert run_r.status_code == 202, run_r.text
    queued = run_r.json()
    assert queued["status"] == "queued"
    assert queued["module_id"] == mod_r.json()["id"]

    run_detail = await admin_client.get(f"/api/admin/runs/{queued['run_id']}")
    assert run_detail.status_code == 200
    run_body = run_detail.json()
    assert run_body["status"] == "completed"
    assert run_body["result"]["briefing"] == "无数据"
    assert run_body["module_snapshot"]["name"] == "test-module"
    assert run_body["prompt_snapshot"]["name"] == "test-tmpl"
    assert run_body["provider_snapshot"]["model"] == "deepseek/deepseek-chat"

    provider_delete = await admin_client.delete(f"/api/admin/providers/{prov_id}")
    assert provider_delete.status_code == 409

    channel_delete = await admin_client.delete(f"/api/admin/channels/{channel_id}")
    assert channel_delete.status_code == 409


@pytest.mark.asyncio
async def test_manual_run_rejects_when_active(admin_client):
    tmpl_r = await admin_client.post(
        "/api/admin/prompts",
        json={"name": "active-tmpl", "template": "Analyze {{ item_count }}.", "output_schema": {}},
    )
    prov_r = await admin_client.post(
        "/api/admin/providers",
        json={"name": "active-prov", "model": "deepseek/deepseek-chat", "api_key": "sk-x"},
    )
    mod_r = await admin_client.post(
        "/api/admin/modules",
        json={
            "name": "active-module",
            "source": "twitter",
            "filter_config": {"time_mode": "rolling"},
            "prompt_template_id": tmpl_r.json()["id"],
            "provider_id": prov_r.json()["id"],
        },
    )
    module_id = mod_r.json()["id"]

    with patch(
        "megatron.web.modules_api._execute_run_background",
        new_callable=AsyncMock,
        return_value=None,
    ):
        first = await admin_client.post(f"/api/admin/modules/{module_id}/run")
        assert first.status_code == 202, first.text
        assert first.json()["status"] == "queued"

        second = await admin_client.post(f"/api/admin/modules/{module_id}/run")
        assert second.status_code == 409
        assert "already has active run" in second.text
