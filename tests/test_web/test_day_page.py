from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from megatron.core.db import async_session_factory
from megatron.core.engine_models import AnalysisRun
from megatron.engine.bundle import BUNDLE_SCHEMA

TOKEN = "dev-day-token-change-me"  # the default, since bootstrap has not run


@pytest.fixture
def client():
    from megatron.web.app import app

    return TestClient(app)


def bundle(date: str = "2026-07-12") -> dict:
    return {
        "schema": BUNDLE_SCHEMA,
        "bundle_id": f"day-{date}",
        "date": date,
        "timezone": "Asia/Shanghai",
        "run_id": 1,
        "caps": {"must_see_push_max": 3, "must_see_push_actual": 1},
        "stats": {
            "ingest_total": 12,
            "by_source": {"twitter_security_list": 12},
            "by_tier": {"must_see_push": 1, "skim": 1},
            "dropped_unmatched": 0,
        },
        "items": [
            {
                "id": 1,
                "source_id": "twitter_security_list",
                "external_id": "e1",
                "tier": "must_see_push",
                "one_liner": "AutoJack 可远程接管本地 agent",
                "why_for_me": "你在跑本地 agent",
                "bullets": ["无需交互", "已有 PoC"],
                "actionability": "try",
                "topics": ["ai_agent"],
                "author": "alice",
                "url": "https://x.com/a/1",
                "content": "full text",
                "metrics": {"like_count": 9},
            },
            {
                "id": 2,
                "source_id": "twitter_security_list",
                "external_id": "e2",
                "tier": "skim",
                "one_liner": "某会议 CFP 开放",
                "why_for_me": "",
                "bullets": [],
                "actionability": "none",
                "topics": [],
                "author": "bob",
                "url": "https://x.com/b/2",
                "content": "cfp",
                "metrics": {},
            },
        ],
        "push_item_ids": ["1"],
        "warnings": [],
    }


async def _make_module() -> int:
    """analysis_runs.module_id is a real FK — the chain has to exist."""
    from megatron.core.engine_models import AnalysisModule, LLMProvider, PromptTemplate

    async with async_session_factory() as session:
        tmpl = PromptTemplate(name="day-tmpl", version=1, template="x", output_schema={})
        prov = LLMProvider(name="day-prov", model="m", api_key="", enabled=True)
        session.add_all([tmpl, prov])
        await session.flush()
        module = AnalysisModule(
            name="day-module",
            source="twitter_security_list",
            prompt_template_id=tmpl.id,
            provider_id=prov.id,
        )
        session.add(module)
        await session.commit()
        return module.id


@pytest.fixture
async def module_id() -> int:
    return await _make_module()


@pytest.fixture
async def stored_run(module_id):
    async with async_session_factory() as session:
        session.add(
            AnalysisRun(module_id=module_id, status="completed", result=bundle(), input_count=12)
        )
        await session.commit()


@pytest.mark.asyncio
async def test_page_renders_with_the_capability_token(client, stored_run):
    r = client.get(f"/day/2026-07-12?k={TOKEN}")
    assert r.status_code == 200
    body = r.text
    assert "AutoJack 可远程接管本地 agent" in body
    assert "你在跑本地 agent" in body
    assert "https://x.com/a/1" in body


@pytest.mark.asyncio
async def test_wrong_token_is_404_not_403(client, stored_run):
    """403 would confirm the date exists. 404 tells an unauthorized reader nothing."""
    r = client.get("/day/2026-07-12?k=wrong")
    assert r.status_code == 404
    assert "AutoJack" not in r.text


@pytest.mark.asyncio
async def test_missing_token_is_404(client, stored_run):
    assert client.get("/day/2026-07-12").status_code == 404


@pytest.mark.asyncio
async def test_page_is_not_indexable(client, stored_run):
    r = client.get(f"/day/2026-07-12?k={TOKEN}")
    assert "noindex" in r.text


@pytest.mark.asyncio
async def test_skim_items_do_not_show_personal_why(client, stored_run):
    r = client.get(f"/day/2026-07-12?k={TOKEN}")
    # Both items render, but only the top tiers get the "why this is for you" block.
    assert "某会议 CFP 开放" in r.text
    assert r.text.count('class="why"') == 1


@pytest.mark.asyncio
async def test_day_with_no_bundle_renders_an_empty_state(client):
    r = client.get(f"/day/2026-01-01?k={TOKEN}")
    assert r.status_code == 200
    assert "还没有日刊" in r.text


@pytest.mark.asyncio
async def test_bad_date_is_404(client):
    assert client.get(f"/day/not-a-date?k={TOKEN}").status_code == 404


@pytest.mark.asyncio
async def test_latest_run_wins_for_a_date(client, module_id):
    """Re-running the day replaces what the page shows."""
    async with async_session_factory() as session:
        old = bundle()
        old["items"][0]["one_liner"] = "STALE"
        session.add(AnalysisRun(module_id=module_id, status="completed", result=old))
        await session.commit()

        fresh = bundle()
        fresh["items"][0]["one_liner"] = "FRESH"
        session.add(AnalysisRun(module_id=module_id, status="completed", result=fresh))
        await session.commit()

    r = client.get(f"/day/2026-07-12?k={TOKEN}")
    assert "FRESH" in r.text
    assert "STALE" not in r.text
