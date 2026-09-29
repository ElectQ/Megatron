from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from megatron.core.engine_models import AnalysisModule, AnalysisRun
from megatron.core.db import async_session_factory
from megatron.web.stats_api import trend


@pytest.mark.asyncio
async def test_trend_groups_runs_by_utc_calendar_date():
    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        from megatron.core.engine_models import LLMProvider, PromptTemplate

        prompt = PromptTemplate(name="trend-prompt", template="x")
        provider = LLMProvider(name="trend-provider", model="m")
        session.add_all([prompt, provider])
        await session.flush()
        module = AnalysisModule(
            name="trend-module",
            source="twitter",
            prompt_template_id=prompt.id,
            provider_id=provider.id,
        )
        session.add(module)
        await session.flush()
        session.add_all(
            [
                AnalysisRun(
                    module_id=module.id,
                    status="completed",
                    started_at=now - timedelta(days=1),
                    prompt_tokens=10,
                    completion_tokens=3,
                    total_cost_usd=0.01,
                ),
                AnalysisRun(
                    module_id=module.id,
                    status="completed",
                    started_at=now,
                    prompt_tokens=20,
                    completion_tokens=7,
                    total_cost_usd=0.02,
                ),
            ]
        )
        await session.commit()
        rows = await trend(2, session)

    assert [row["runs"] for row in rows] == [1, 1]
    assert [row["tokens"] for row in rows] == [13, 27]
    assert [row["cost_usd"] for row in rows] == [0.01, 0.02]
