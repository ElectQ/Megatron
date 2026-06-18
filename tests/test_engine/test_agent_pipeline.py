from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from megatron.core.engine_models import (
    AnalysisModule,
    DeliveryLog,
    LLMProvider,
    PromptTemplate,
    WebhookChannel,
)
from megatron.core.db import async_session_factory
from megatron.core.models import ItemRecord
from megatron.engine.runner import ModuleRunner


def _make_item(content="test", like=10):
    from datetime import datetime, timezone

    return ItemRecord(
        item_id="agent-e2e-1",
        source="twitter",
        source_ref="list1",
        content=content,
        url="https://x.com/a/status/1",
        author="alice",
        published_at=datetime.now(timezone.utc),
        collected_at=datetime.now(timezone.utc),
        metrics={"like_count": like, "retweet_count": 0, "reply_count": 0, "view_count": 100},
    )


@pytest.mark.asyncio
async def test_agent_pipeline_records_tool_calls():
    async with async_session_factory() as session:
        tmpl = PromptTemplate(
            name="a-tmpl", version=1, template="Analyze {{ item_count }} items.", output_schema={}
        )
        prov = LLMProvider(name="a-prov", model="deepseek/deepseek-chat", api_key="", enabled=True)
        session.add_all([tmpl, prov])
        await session.flush()
        module = AnalysisModule(
            name="agent-module",
            source="twitter",
            filter_config={"time_mode": "rolling", "window_hours": 24, "max_items": 5},
            prompt_template_id=tmpl.id,
            provider_id=prov.id,
            agent_backend="lite",
            tools_config=[{"name": "fetch_url", "enabled": True}],
            enabled=True,
        )
        session.add_all([module, _make_item("CVE-2024-1 RCE")])
        await session.commit()

        from megatron.engine.agent import AgentResult
        from megatron.engine import agent_loop

        fake_result = AgentResult(
            content='{"briefing":"analyzed","items":[]}',
            prompt_tokens=50,
            completion_tokens=20,
            cost_usd=0.003,
            tool_calls=[{"turn": 1, "tool": "fetch_url", "ok": True, "args": {"url": "http://x"}}],
            turns=2,
        )
        original_run = agent_loop.LiteAgentLoop.run
        agent_loop.LiteAgentLoop.run = AsyncMock(return_value=fake_result)
        try:
            runner = ModuleRunner(session)
            summary = await runner.run_module(module.id)
        finally:
            agent_loop.LiteAgentLoop.run = original_run

        assert summary["status"] == "completed"
        assert len(summary["tool_calls"]) == 1
        assert summary["tool_calls"][0]["tool"] == "fetch_url"


@pytest.mark.asyncio
async def test_delivery_to_mocked_channel():
    async with async_session_factory() as session:
        tmpl = PromptTemplate(name="d-tmpl", version=1, template="test", output_schema={})
        prov = LLMProvider(name="d-prov", model="m", api_key="", enabled=True)
        ch = WebhookChannel(
            name="tg-test",
            kind="telegram",
            config={"bot_token": "fake", "chat_id": "123"},
            enabled=True,
        )
        session.add_all([tmpl, prov, ch])
        await session.flush()
        module = AnalysisModule(
            name="deliv-module",
            source="twitter",
            filter_config={"time_mode": "rolling", "window_hours": 24, "max_items": 5},
            prompt_template_id=tmpl.id,
            provider_id=prov.id,
            agent_backend="none",
            webhook_channel_ids=[ch.id],
            enabled=True,
        )
        session.add_all([module, _make_item()])
        await session.commit()

        from megatron.llm.provider import ChatResponse

        with (
            patch(
                "megatron.llm.provider.LLMProvider.chat",
                new_callable=AsyncMock,
                return_value=ChatResponse(
                    content='{"briefing":"hi","items":[]}', prompt_tokens=5, completion_tokens=2
                ),
            ),
            patch(
                "megatron.plugins.webhooks.telegram.TelegramChannel.send",
                new_callable=AsyncMock,
                return_value={"ok": True, "status_code": 200, "error": ""},
            ),
        ):
            runner = ModuleRunner(session)
            summary = await runner.run_module(module.id)

        assert summary["status"] == "completed"
        assert "deliveries" in summary["result"]
        assert summary["result"]["deliveries"][0]["ok"] is True

        logs = await session.execute(
            select(DeliveryLog).where(DeliveryLog.run_id == summary["run_id"])
        )
        delivery_logs = logs.scalars().all()
        assert len(delivery_logs) == 1
        assert delivery_logs[0].status == "sent"
