from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from megatron.core.engine_models import AnalysisRun
from megatron.core.models import ItemRecord
from megatron.core.db import async_session_factory
from megatron.engine.runner import ModuleRunner
from megatron.llm.provider import ChatResponse


@pytest.fixture
def sample_items():
    now = datetime.now(timezone.utc)
    return [
        ItemRecord(
            item_id="r-e2e-1",
            source="twitter",
            source_ref="list1",
            content="Critical CVE-2024-9999 RCE in apache",
            url="https://x.com/a/status/1",
            author="alice",
            author_name="Alice",
            published_at=now - timedelta(hours=1),
            collected_at=now,
            metrics={"like_count": 20, "retweet_count": 5, "reply_count": 2, "view_count": 1000},
        ),
        ItemRecord(
            item_id="r-e2e-2",
            source="twitter",
            source_ref="list1",
            content="good morning everyone",
            url="https://x.com/b/status/2",
            author="bob",
            author_name="Bob",
            published_at=now - timedelta(hours=2),
            collected_at=now,
            metrics={"like_count": 0, "retweet_count": 0, "reply_count": 0, "view_count": 100},
        ),
    ]


@pytest.mark.asyncio
async def test_full_pipeline_with_mock_llm(sample_items):
    async with async_session_factory() as session:
        from megatron.core.engine_models import LLMProvider, PromptTemplate, AnalysisModule

        tmpl = PromptTemplate(
            name="e2e-tmpl",
            version=1,
            template="Analyze {{ item_count }} items.",
            output_schema={},
            is_active=True,
        )
        prov = LLMProvider(
            name="e2e-prov",
            model="deepseek/deepseek-chat",
            api_key="",
            temperature=0.3,
            max_tokens=1024,
            enabled=True,
        )
        session.add_all([tmpl, prov])
        await session.flush()
        module = AnalysisModule(
            name="e2e-module",
            source="twitter",
            source_ref="",
            filter_config={
                "time_mode": "rolling",
                "window_hours": 24,
                "filters": [{"name": "interaction", "config": {"threshold": 1}}],
                "max_items": 10,
            },
            prompt_template_id=tmpl.id,
            provider_id=prov.id,
            agent_backend="none",
            enabled=True,
        )
        session.add(module)
        session.add_all(sample_items)
        await session.commit()

        mock_resp = ChatResponse(
            content='{"briefing": "今日有 1 个重要 CVE", "items": [{"title":"Apache RCE","cve":"CVE-2024-9999","severity":"high","summary":"严重漏洞","url":"https://x.com/a/status/1"}]}',
            prompt_tokens=150,
            completion_tokens=80,
            cost_usd=0.002,
        )

        with patch(
            "megatron.llm.provider.LLMProvider.chat",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            runner = ModuleRunner(session)
            summary = await runner.run_module(module.id, triggered_by="test")

        assert summary["status"] == "completed"
        assert summary["input_count"] == 2
        assert summary["prompt_tokens"] == 150
        assert summary["completion_tokens"] == 80
        assert summary["cost_usd"] == 0.002
        assert "briefing" in summary["result"]
        assert summary["result"]["items"][0]["cve"] == "CVE-2024-9999"
        assert summary["module_snapshot"]["name"] == "e2e-module"
        assert summary["prompt_snapshot"]["name"] == "e2e-tmpl"
        assert summary["provider_snapshot"]["model"] == "deepseek/deepseek-chat"
        assert "api_key" not in summary["provider_snapshot"]
        assert (
            summary["rendered_prompt_hash"]
            == hashlib.sha256("Analyze 1 items.".encode()).hexdigest()
        )

        run = await session.get(AnalysisRun, summary["run_id"])
        assert run.status == "completed"
        assert run.input_count == 2
        assert run.module_snapshot["filter_config"]["max_items"] == 10
        assert run.prompt_snapshot["template"] == "Analyze {{ item_count }} items."
        assert run.provider_snapshot["temperature"] == 0.3
        assert run.rendered_prompt_hash == summary["rendered_prompt_hash"]


@pytest.mark.asyncio
async def test_create_run_rejects_active_run():
    from megatron.core.engine_models import AnalysisModule, LLMProvider, PromptTemplate
    from megatron.engine.runner import ActiveRunExists

    async with async_session_factory() as session:
        tmpl = PromptTemplate(name="lock-tmpl", version=1, template="test", output_schema={})
        prov = LLMProvider(name="lock-prov", model="m", api_key="", enabled=True)
        session.add_all([tmpl, prov])
        await session.flush()
        module = AnalysisModule(
            name="lock-module",
            source="twitter",
            filter_config={"time_mode": "rolling"},
            prompt_template_id=tmpl.id,
            provider_id=prov.id,
            enabled=True,
        )
        session.add(module)
        await session.commit()

        runner = ModuleRunner(session)
        first = await runner.create_run(module.id)
        assert first["status"] == "queued"

        with pytest.raises(ActiveRunExists) as exc:
            await runner.create_run(module.id)

        assert exc.value.run_id == first["run_id"]
