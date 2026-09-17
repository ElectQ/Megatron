"""An answer that never parsed must not be delivered as "a quiet day".

The failure this pins down: `deepseek-chat` was retired, so the API returned an
empty body. The runner parsed nothing, the bundle came out with zero tiers, and
the digest rendered over 25 ingested items still read

    入库 25 · 必看 0 · 推荐 0

    今日无必看条目。

which a reader cannot tell apart from a genuinely quiet day. Worse, `_deliver`
never passed `parse_error` to the channels at all, so every plugin's error path
was dead code — the one signal saying "the model's answer was unusable" was
dropped on the floor between the runner and the webhook.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from megatron.core.db import async_session_factory
from megatron.core.engine_models import AnalysisModule, LLMProvider, PromptTemplate
from megatron.core.models import ItemRecord
from megatron.engine.runner import ModuleRunner
from megatron.llm.provider import ChatResponse

CVE = "Critical CVE-2024-9999 RCE"


@pytest.fixture
async def bundle_module():
    """A day_bundle task over three ingested items, plus a run ready to execute."""
    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        tmpl = PromptTemplate(
            name="unparsed-tmpl",
            version=1,
            template="Analyze {{ item_count }} items.",
            output_schema={},
            is_active=True,
        )
        prov = LLMProvider(
            name="unparsed-prov",
            model="deepseek/deepseek-v4-flash",
            api_key="",
            temperature=0.3,
            max_tokens=1024,
            enabled=True,
        )
        session.add_all([tmpl, prov])
        await session.flush()
        module = AnalysisModule(
            name="unparsed-module",
            source="twitter",
            source_ref="",
            filter_config={
                "time_mode": "rolling",
                "window_hours": 24,
                "max_items": 0,
                "output_mode": "day_bundle",
            },
            prompt_template_id=tmpl.id,
            provider_id=prov.id,
            agent_backend="none",
            enabled=True,
        )
        session.add(module)
        for i in range(3):
            session.add(
                ItemRecord(
                    item_id=f"u-{i}",
                    source="twitter",
                    source_ref="list1",
                    content=f"{CVE} #{i}",
                    url=f"https://x.com/a/status/{i}",
                    author="alice",
                    author_name="Alice",
                    published_at=now,
                    collected_at=now,
                )
            )
        await session.commit()
        return module.id


async def _run(module_id: int, content: str, session):
    with patch(
        "megatron.llm.provider.LLMProvider.chat",
        new_callable=AsyncMock,
        return_value=ChatResponse(content=content, prompt_tokens=10, completion_tokens=0),
    ):
        runner = ModuleRunner(session)
        return await runner.run_module(module_id, triggered_by="test")


@pytest.mark.asyncio
async def test_an_empty_completion_is_not_pushed_as_a_quiet_day(bundle_module):
    """The exact production failure: empty body, 3 items ingested."""
    async with async_session_factory() as session:
        summary = await _run(bundle_module, "", session)

        assert summary["status"] == "completed"
        assert summary["input_count"] == 3
        result = summary["result"]

        # The bundle is still built — the day page renders it and shows the warning.
        assert result["schema"] == "day_bundle_v1"
        assert result["stats"]["ingest_total"] == 3
        # But it must not claim the day was quiet, and it must carry the failure.
        assert result["parse_error"]
        assert result["report_markdown"] == ""
        assert any(w["code"] == "llm_output_unparsed" for w in result["warnings"])


@pytest.mark.asyncio
async def test_the_channels_are_told_the_answer_was_unusable(bundle_module):
    """`parse_error` reaches AnalysisResult, so each plugin sends its error notice
    instead of a blank digest. Without it every channel's error branch was dead."""
    from sqlalchemy import select

    from megatron.core.engine_models import AnalysisRun, WebhookChannel
    from megatron.engine.delivery import DeliveryService

    async with async_session_factory() as session:
        channel = WebhookChannel(
            name="test-channel", kind="dingtalk", config={"webhook_url": "x"}, enabled=True
        )
        session.add(channel)
        await session.flush()
        module = (
            await session.execute(select(AnalysisModule).where(AnalysisModule.id == bundle_module))
        ).scalar_one()
        module.webhook_channel_ids = [channel.id]
        await session.commit()

        summary = await _run(bundle_module, "", session)
        run = await session.get(AnalysisRun, summary["run_id"])

        captured: list = []

        async def fake_send(_self, ch, result, run_id):
            captured.append(result)
            return {"ok": True, "status_code": 200, "error": ""}

        with patch.object(DeliveryService, "_send_one", fake_send):
            await DeliveryService(session).deliver(
                module, run, ModuleRunner(session)._analysis_result(module, run, run.result)
            )

    assert len(captured) == 1
    ar = captured[0]
    assert ar.parse_error, "the channels must be able to see the parse failure"
    assert ar.report_markdown == ""
    # Which is exactly the branch the plugins take to send an error notice.
    assert ar.parse_error and not ar.report_markdown


@pytest.mark.asyncio
async def test_an_unparseable_answer_keeps_what_it_can(bundle_module):
    """When nothing parses, whatever text the model produced is still what gets
    sent — an empty `report_markdown` is what triggers the channels' error notice."""
    async with async_session_factory() as session:
        summary = await _run(bundle_module, "I cannot help with that.", session)
        result = summary["result"]

    assert result["parse_error"]
    assert result["report_markdown"] == ""
    assert result["items"] == []


@pytest.mark.asyncio
async def test_a_parsed_answer_still_renders_the_normal_push(bundle_module):
    """The guard is specific to unparsed output — a good answer is untouched."""
    content = (
        '{"items": ['
        '{"source_id": "twitter", "external_id": "u-0", "tier": "must_see_push",'
        ' "one_liner": "Apache RCE", "why_for_me": "你的栈里有它", "url": "https://x.com/a/status/0"},'
        '{"source_id": "twitter", "external_id": "u-1", "tier": "recommend",'
        ' "one_liner": "另一个 CVE", "why_for_me": "-", "url": "https://x.com/a/status/1"}'
        "]}"
    )
    async with async_session_factory() as session:
        summary = await _run(bundle_module, content, session)
        result = summary["result"]

    assert not result.get("parse_error")
    assert "🔴 **必看**" in result["report_markdown"]
    assert result["push_item_ids"], "a good answer still produces a push"
