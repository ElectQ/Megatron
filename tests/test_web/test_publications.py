"""Operator override of what the public blog shows.

The analysis decides `public` per item; these tests pin the operator's ability to
overrule it — and, critically, that overruling never rewrites the run (the record
of what the model actually said) and never leaks a private item.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from megatron.core.db import async_session_factory
from megatron.engine.bundle import BUNDLE_SCHEMA
from megatron.web.public_view import (
    has_public,
    load_policy,
    public_items,
    public_recent,
)

SRC = "twitter_security_list"
DATE = "2026-07-13"
TOKEN = "dev-admin-token-change-me"  # bootstrap hasn't run in tests


@pytest.fixture
def client():
    from megatron.web.app import app

    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {TOKEN}"})
    return c


def _item(i, public, tier="recommend"):
    return {
        "id": str(i),
        "source_id": SRC,
        "tier": tier,
        "one_liner": f"公开条目 {i}" if public else f"私密条目 {i}",
        "why_for_me": f"secret-rationale-{i}",
        "scores": {"relevance": 3},
        "topics": ["t"],
        "public": public,
        "url": f"https://example.com/{i}",
        "content": "c",
    }


def _bundle(items):
    return {
        "schema": BUNDLE_SCHEMA,
        "source_id": SRC,
        "date": DATE,
        "title": "推特安全流",
        "items": items,
        "push_item_ids": [],
    }


async def _seed_source(source_id=SRC, audience="public"):
    from megatron.core.models import SourceConfig

    async with async_session_factory() as s:
        s.add(
            SourceConfig(
                name=source_id, source_type="t", adapter="bundle_pull", audience=audience
            )
        )
        await s.commit()


async def _seed_run(items, source_id=SRC, audience="public"):
    from megatron.core.engine_models import (
        AnalysisModule,
        AnalysisRun,
        LLMProvider,
        PromptTemplate,
    )

    await _seed_source(source_id, audience)
    async with async_session_factory() as s:
        prompt = PromptTemplate(name="p", template="x")
        provider = LLMProvider(name="llm", model="m")
        s.add_all([prompt, provider])
        await s.commit()
        await s.refresh(prompt)
        await s.refresh(provider)
        m = AnalysisModule(
            name="mod", source=SRC, prompt_template_id=prompt.id, provider_id=provider.id
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        run = AnalysisRun(
            module_id=m.id,
            status="completed",
            result=_bundle(items),
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
        s.add(run)
        await s.commit()
        await s.refresh(run)
        return run.id


@pytest.mark.asyncio
async def test_inside_a_public_source_absent_means_publish():
    """The default flipped: content here is already-public news, so a day publishes
    unless the model explicitly holds an item back."""
    await _seed_run([_item(1, None), _item(2, True), _item(3, False)])
    async with async_session_factory() as s:
        policy = await load_policy(s)
        pub = public_items(_bundle([_item(1, None), _item(2, True), _item(3, False)]), policy)
    # 1 (didn't say) and 2 (said yes) publish; 3 (explicitly held back) does not.
    assert [p["id"] for p in pub] == ["1", "2"]


@pytest.mark.asyncio
async def test_a_personal_source_never_publishes_whatever_the_model_said():
    """The hard gate: the GitHub feed's events are each public, but publishing them
    exposes who you follow — so the source never reaches the blog."""
    await _seed_run([_item(1, True), _item(2, True)], source_id="gh", audience="personal")
    b = _bundle([_item(1, True), _item(2, True)])
    b["source_id"] = "gh"
    async with async_session_factory() as s:
        policy = await load_policy(s)
        assert public_items(b, policy) == []
        assert has_public(b, policy) is False


@pytest.mark.asyncio
async def test_public_items_carry_the_take_but_not_the_scores():
    """The take is why the blog is worth reading; the scores are internal."""
    await _seed_source()
    async with async_session_factory() as s:
        policy = await load_policy(s)
        pub = public_items(_bundle([_item(1, True)]), policy)
    assert pub and pub[0]["why_for_me"] == "secret-rationale-1"
    assert "scores" not in pub[0]


@pytest.mark.asyncio
async def test_operator_can_drop_a_single_item(client):
    await _seed_run([_item(1, True), _item(2, True)])

    r = client.put(f"/api/admin/publications/{SRC}/{DATE}/items/1", json={"published": False})
    assert r.status_code == 200
    day = r.json()
    assert day["public_count"] == 1
    assert day["live"] is True  # the other item keeps the day alive

    item1 = next(i for i in day["items"] if i["id"] == "1")
    # The operator's call wins, but the model's original call is still recorded.
    assert item1["public"] is False
    assert item1["public_by_model"] is True
    assert item1["overridden"] is True

    async with async_session_factory() as s:
        policy = await load_policy(s)
        pub = public_items(_bundle([_item(1, True), _item(2, True)]), policy)
    assert [p["id"] for p in pub] == ["2"]


@pytest.mark.asyncio
async def test_taking_the_day_down_hides_it_entirely(client):
    await _seed_run([_item(1, True), _item(2, True)])

    r = client.put(f"/api/admin/publications/{SRC}/{DATE}", json={"published": False})
    assert r.status_code == 200
    assert r.json()["live"] is False

    b = _bundle([_item(1, True), _item(2, True)])
    async with async_session_factory() as s:
        policy = await load_policy(s)
        assert public_items(b, policy) == []
        assert has_public(b, policy) is False
        assert await public_recent(s) == []  # gone from the blog index too

    # ...and the public page 404s.
    assert client.get(f"/zh/{SRC}/{DATE}").status_code == 404


@pytest.mark.asyncio
async def test_operator_can_publish_what_the_model_held_back(client):
    await _seed_run([_item(1, False)])

    r = client.put(f"/api/admin/publications/{SRC}/{DATE}/items/1", json={"published": True})
    assert r.status_code == 200
    assert r.json()["live"] is True

    async with async_session_factory() as s:
        policy = await load_policy(s)
        pub = public_items(_bundle([_item(1, False)]), policy)
    # Force-published — take and all; only the internal scores are held back.
    assert [p["id"] for p in pub] == ["1"]
    assert "scores" not in pub[0]


@pytest.mark.asyncio
async def test_reset_hands_the_decision_back_to_the_analysis(client):
    await _seed_run([_item(1, True)])
    client.put(f"/api/admin/publications/{SRC}/{DATE}", json={"published": False})

    r = client.delete(f"/api/admin/publications/{SRC}/{DATE}")
    assert r.status_code == 200
    assert r.json()["live"] is True  # back to what the model said

    async with async_session_factory() as s:
        policy = await load_policy(s)
        assert policy.overrides.days == {} and policy.overrides.items == {}


@pytest.mark.asyncio
async def test_run_result_is_never_rewritten(client):
    """The override must not touch the run — it is the record of what the model said."""
    run_id = await _seed_run([_item(1, True), _item(2, False)])

    client.put(f"/api/admin/publications/{SRC}/{DATE}/items/1", json={"published": False})
    client.put(f"/api/admin/publications/{SRC}/{DATE}/items/2", json={"published": True})
    client.put(f"/api/admin/publications/{SRC}/{DATE}", json={"published": False})

    from megatron.core.engine_models import AnalysisRun

    async with async_session_factory() as s:
        run = await s.get(AnalysisRun, run_id)
        flags = {i["id"]: i["public"] for i in run.result["items"]}
    assert flags == {"1": True, "2": False}  # exactly what the model produced


@pytest.mark.asyncio
async def test_admin_listing_shows_model_vs_effective(client):
    await _seed_run([_item(1, True), _item(2, False)])
    client.put(f"/api/admin/publications/{SRC}/{DATE}/items/1", json={"published": False})

    days = client.get("/api/admin/publications").json()
    day = next(d for d in days if d["source_id"] == SRC and d["date"] == DATE)
    assert day["total"] == 2
    assert day["public_count"] == 0
    assert day["live"] is False  # nothing public left → not on the blog
    by_id = {i["id"]: i for i in day["items"]}
    assert by_id["1"]["public_by_model"] is True and by_id["1"]["public"] is False
    assert by_id["2"]["public_by_model"] is False and by_id["2"]["public"] is False
