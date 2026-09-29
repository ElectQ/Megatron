"""Adaptive batching keeps long days inside the model's completion limit.

daily_intel_v1 asks the model to echo every input back as JSON. On a busy day
the output array hits the completion limit, the tail of the day is never tiered
and the run ends with "LLM JSON truncated; recovered partial response". Batching
splits the day into several LLM calls, bisects a still-truncated batch, and
merges the answers — caps are enforced over the merge, so the push stays the
day's best rather than per-batch best.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from megatron.core.db import async_session_factory
from megatron.core.engine_models import AnalysisModule, LLMProvider, PromptTemplate
from megatron.core.models import ItemRecord
from megatron.engine.runner import ModuleRunner
from megatron.llm.provider import ChatResponse


def _output_for(ids: list[str]) -> str:
    return json.dumps(
        {
            "items": [
                {
                    "external_id": i,
                    "source_id": "list1",
                    "tier": "skim",
                    "one_liner": f"item {i}",
                    "topics": ["x"],
                }
                for i in ids
            ]
        }
    )


@pytest.fixture
async def batch_module():
    """A day_bundle task over 5 items (batch_size=2 → 3 batches), plus a run."""
    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        tmpl = PromptTemplate(
            name="batch-tmpl",
            version=1,
            template="Tier {{ item_count }} items.",
            output_schema={},
            is_active=True,
        )
        prov = LLMProvider(
            name="batch-prov",
            model="test-model",
            api_key="",
            temperature=0.3,
            max_tokens=1024,
            enabled=True,
        )
        session.add_all([tmpl, prov])
        await session.flush()
        module = AnalysisModule(
            name="batch-module",
            source="twitter",
            source_ref="",
            filter_config={
                "time_mode": "rolling",
                "window_hours": 24,
                "output_mode": "day_bundle",
                "analysis_batch_size": 2,
            },
            prompt_template_id=tmpl.id,
            provider_id=prov.id,
            agent_backend="none",
            enabled=True,
        )
        session.add(module)
        for i in range(5):
            session.add(
                ItemRecord(
                    item_id=f"b-{i}",
                    source="twitter",
                    source_ref="list1",
                    content=f"CVE-{i}",
                    url=f"https://x.com/a/{i}",
                    author="a",
                    published_at=now,
                    collected_at=now,
                )
            )
        await session.commit()
        return module.id


async def _run(module_id: int, contents: list[str] | Exception, session):
    calls = {"n": 0}

    def _content():
        if isinstance(contents, Exception):
            raise contents
        return contents[min(calls["n"], len(contents) - 1)]

    async def fake_chat(messages, **kwargs):
        calls["n"] += 1
        if isinstance(contents, Exception):
            raise contents
        return ChatResponse(
            content=contents[min(calls["n"] - 1, len(contents) - 1)],
            prompt_tokens=10,
            completion_tokens=5,
        )

    with patch(
        "megatron.llm.provider.LLMProvider.chat",
        new_callable=AsyncMock,
        side_effect=fake_chat,
    ):
        runner = ModuleRunner(session)
        return await runner.run_module(module_id, triggered_by="test"), calls


@pytest.mark.asyncio
async def test_long_day_is_tiered_in_batches_and_merged(batch_module):
    """5 items, batch 2 → 3 calls; the merged result tiers all 5."""
    async with async_session_factory() as session:
        summary, calls = await _run(
            batch_module,
            [_output_for(["b-0", "b-1"]), _output_for(["b-2", "b-3"]), _output_for(["b-4"])],
            session,
        )
        result = summary["result"]

    assert calls["n"] == 3
    assert not result.get("parse_error")
    assert result["stats"]["ingest_total"] == 5
    tiered = {it["external_id"] for it in result["items"]}
    assert tiered == {"b-0", "b-1", "b-2", "b-3", "b-4"}


@pytest.mark.asyncio
async def test_a_truncated_batch_is_bisected(batch_module):
    """First call (2 items) comes back truncated → split into singles and retry."""
    truncated = '{"items":[{"external_id":"b-0","source_id":"list1","tier":"ski'
    async with async_session_factory() as session:
        summary, calls = await _run(
            batch_module,
            [
                truncated,  # batch [b-0, b-1] → truncated
                _output_for(["b-0"]),  # retry half 1
                _output_for(["b-1"]),  # retry half 2
                _output_for(["b-2", "b-3"]),
                _output_for(["b-4"]),
            ],
            session,
        )
        result = summary["result"]

    assert calls["n"] == 5
    tiered = {it["external_id"] for it in result["items"]}
    assert tiered == {"b-0", "b-1", "b-2", "b-3", "b-4"}
    assert not result.get("parse_error")


@pytest.mark.asyncio
async def test_small_days_stay_in_one_call(batch_module):
    """≤ batch_size items never enter the batch path — one call, old behaviour."""
    async with async_session_factory() as session:
        module = await session.get(AnalysisModule, batch_module)
        module.filter_config = {**module.filter_config, "analysis_batch_size": 10}
        await session.commit()

        summary, calls = await _run(batch_module, [_output_for([f"b-{i}" for i in range(5)])], session)
        result = summary["result"]

    assert calls["n"] == 1
    assert not result.get("parse_error")
    assert result["stats"]["ingest_total"] == 5


@pytest.mark.asyncio
async def test_a_failed_single_item_degrades_to_skim(batch_module):
    """Even a single item can fail twice; it lands as skim (page-only), never lost."""
    async with async_session_factory() as session:
        summary, calls = await _run(batch_module, RuntimeError("api down"), session)
        result = summary["result"]

    assert result.get("parse_error") or result["stats"]["ingest_total"] == 5
    tiered = {it["external_id"] for it in result["items"]}
    assert tiered == {"b-0", "b-1", "b-2", "b-3", "b-4"}


@pytest.mark.asyncio
async def test_batch_tokens_are_accounted(batch_module):
    async with async_session_factory() as session:
        summary, calls = await _run(
            batch_module,
            [_output_for(["b-0", "b-1"]), _output_for(["b-2", "b-3"]), _output_for(["b-4"])],
            session,
        )

    assert summary["prompt_tokens"] == 30
    assert summary["completion_tokens"] == 15
    assert calls["n"] == 3
