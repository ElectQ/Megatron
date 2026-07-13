"""The product profile is files, not framework constants — so it is loadable and
seedable independently, and these tests pin that contract.

Seed semantics: create-if-missing. A prompt/task edited in the admin UI must never
be clobbered by the file on the next boot.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from megatron.core.engine_models import (
    AnalysisModule,
    LLMProvider,
    PromptTemplate,
    WebhookChannel,
)
from megatron.profile.loader import (
    load_prompt_specs,
    load_task_specs,
    seed_prompts,
    seed_tasks,
    seed_profile,
)
from megatron.profile.spec import PromptSpec, TaskSpec


def _write(dirpath, name, text):
    p = dirpath / name
    p.write_text(text)
    return p


# ------------------------------------------------------------------ parsing


def test_a_prompt_file_is_frontmatter_plus_body(tmp_path):
    d = tmp_path / "prompts"
    d.mkdir()
    _write(
        d,
        "x.md",
        "---\nname: demo\ndisplay_name: 示例\noutput_schema: daily_intel_v1\n---\nHello {{ now }}",
    )
    specs, errors = load_prompt_specs(d)
    assert not errors
    assert specs[0].name == "demo"
    assert specs[0].display_name == "示例"
    assert specs[0].output_schema == "daily_intel_v1"
    assert specs[0].body == "Hello {{ now }}"


def test_a_prompt_without_frontmatter_is_a_reported_error_not_a_crash(tmp_path):
    d = tmp_path / "prompts"
    d.mkdir()
    _write(d, "bad.md", "no frontmatter here")
    specs, errors = load_prompt_specs(d)
    assert not specs
    assert len(errors) == 1
    assert "frontmatter" in str(errors[0])


def test_a_task_file_parses_names_and_filter_config(tmp_path):
    d = tmp_path / "tasks"
    d.mkdir()
    _write(
        d,
        "t.yaml",
        "name: mytask\nsource: mysrc\nprompt: myprompt\nchannels: []\n"
        "filter_config:\n  output_mode: day_bundle\n",
    )
    specs, errors = load_task_specs(d)
    assert not errors
    assert specs[0].source == "mysrc"
    assert specs[0].prompt == "myprompt"
    assert specs[0].filter_config["output_mode"] == "day_bundle"


def test_the_shipped_profile_parses_clean():
    p_specs, p_err = load_prompt_specs("config/prompts")
    t_specs, t_err = load_task_specs("config/tasks")
    assert not p_err and not t_err
    assert {s.name for s in p_specs} >= {"daily_intel_v1", "github_radar_v1"}
    assert {s.name for s in t_specs} >= {"twitter_security_briefing", "github_followee_briefing"}


# ------------------------------------------------------------------ seeding


@pytest_asyncio.fixture
async def session():
    from megatron.core.db import async_session_factory

    async with async_session_factory() as s:
        yield s


async def _provider(session, name="deepseek"):
    p = LLMProvider(
        name=name,
        model="m",
        api_base="http://x",
        api_key="k",
        temperature=0.3,
        max_tokens=1000,
        enabled=True,
    )
    session.add(p)
    await session.commit()
    return p


@pytest.mark.asyncio
async def test_seed_prompts_creates_then_skips(session):
    spec = PromptSpec(name="p1", output_schema="daily_intel_v1", body="body")

    r1 = await seed_prompts(session, [spec])
    assert r1["seeded"] == ["p1"]
    row = (
        await session.execute(select(PromptTemplate).where(PromptTemplate.name == "p1"))
    ).scalar_one()
    assert row.output_schema  # resolved from the name

    r2 = await seed_prompts(session, [spec])
    assert r2["seeded"] == [] and r2["skipped"] == ["p1"]


@pytest.mark.asyncio
async def test_a_ui_edit_is_never_overwritten_by_the_file(session):
    await seed_prompts(session, [PromptSpec(name="p2", output_schema="", body="original")])
    row = (
        await session.execute(select(PromptTemplate).where(PromptTemplate.name == "p2"))
    ).scalar_one()
    row.template = "edited in the UI"
    await session.commit()

    await seed_prompts(session, [PromptSpec(name="p2", output_schema="", body="original")])
    row = (
        await session.execute(select(PromptTemplate).where(PromptTemplate.name == "p2"))
    ).scalar_one()
    assert row.template == "edited in the UI", "the file is a seed, the DB is the truth"


@pytest.mark.asyncio
async def test_seed_tasks_resolves_prompt_provider_and_channels(session):
    await _provider(session)
    await seed_prompts(session, [PromptSpec(name="pr", output_schema="", body="b")])
    ch = WebhookChannel(name="钉钉", kind="dingtalk", config={}, enabled=True)
    session.add(ch)
    await session.commit()

    spec = TaskSpec(name="task1", source="src", prompt="pr", channels=["钉钉"])
    r = await seed_tasks(session, [spec])
    assert r["seeded"] == ["task1"]
    mod = (
        await session.execute(select(AnalysisModule).where(AnalysisModule.name == "task1"))
    ).scalar_one()
    assert mod.prompt_template_id is not None
    assert mod.provider_id is not None
    assert mod.webhook_channel_ids == [ch.id]


@pytest.mark.asyncio
async def test_a_task_with_no_provider_is_skipped_not_crashed(session):
    await seed_prompts(session, [PromptSpec(name="pr2", output_schema="", body="b")])
    spec = TaskSpec(name="task2", source="src", prompt="pr2")  # no provider seeded
    r = await seed_tasks(session, [spec])
    assert r["seeded"] == [] and "task2" in r["unresolved"]


@pytest.mark.asyncio
async def test_seed_profile_end_to_end_from_the_shipped_files(session):
    await _provider(session)
    result = await seed_profile(session, "config")
    assert not result["errors"]
    assert "daily_intel_v1" in result["prompts"]["seeded"]
    # page-only github task binds no channels
    gh = (
        await session.execute(
            select(AnalysisModule).where(AnalysisModule.name == "github_followee_briefing")
        )
    ).scalar_one()
    assert gh.webhook_channel_ids == []
    assert gh.filter_config["caps"]["must_see_max"] == 10
