"""The scheduled-analysis acquire-with-retry loop (scheduler._run_module_job).

A polled 'today' source may not have landed by the task's fire time, so the job
re-pulls hourly up to ACQUIRE_MAX_ATTEMPTS. These tests drive the orchestration
directly — pull / presence / run are stubbed — so no HTTP and no real sleeps.
"""

from __future__ import annotations

import asyncio

import pytest

import megatron.scheduler as sched


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _instant(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", _instant)


def _wire(monkeypatch, *, present_on):
    """Stub the loop's collaborators. `present_on` is the attempt number at which
    today's data appears (None = never). Returns a calls dict to assert on."""
    calls = {"poll": 0, "run": 0, "fail": 0}

    async def fake_plan(_module_id):
        return (True, "src1")

    async def fake_poll(_source_id):
        calls["poll"] += 1
        return (1, 0)

    async def fake_present(_module_id):
        return present_on is not None and calls["poll"] >= present_on

    async def fake_run(_module_id, _name):
        calls["run"] += 1

    async def fake_fail(_module_id, _name):
        calls["fail"] += 1

    monkeypatch.setattr(sched, "_acquire_plan", fake_plan)
    monkeypatch.setattr(sched, "poll_source", fake_poll)
    monkeypatch.setattr(sched, "_today_present", fake_present)
    monkeypatch.setattr(sched, "_do_module_run", fake_run)
    monkeypatch.setattr(sched, "_record_failed_day", fake_fail)
    return calls


@pytest.mark.asyncio
async def test_analyses_on_first_attempt_when_data_ready(monkeypatch):
    calls = _wire(monkeypatch, present_on=1)
    await sched._run_module_job(1, "m")
    assert calls == {"poll": 1, "run": 1, "fail": 0}


@pytest.mark.asyncio
async def test_retries_then_analyses_when_data_lands_late(monkeypatch):
    calls = _wire(monkeypatch, present_on=3)
    await sched._run_module_job(1, "m")
    # Pulled three times, analysed once on the third, never marked failed.
    assert calls == {"poll": 3, "run": 1, "fail": 0}


@pytest.mark.asyncio
async def test_marks_day_failed_after_five_empty_attempts(monkeypatch):
    calls = _wire(monkeypatch, present_on=None)
    await sched._run_module_job(1, "m")
    assert calls["poll"] == sched.ACQUIRE_MAX_ATTEMPTS
    assert calls["run"] == 0
    assert calls["fail"] == 1


@pytest.mark.asyncio
async def test_pull_error_counts_as_a_failed_attempt_not_a_crash(monkeypatch):
    calls = _wire(monkeypatch, present_on=None)

    async def boom(_source_id):
        calls["poll"] += 1
        raise RuntimeError("network down")

    monkeypatch.setattr(sched, "poll_source", boom)
    await sched._run_module_job(1, "m")
    # Still exhausts the budget and fails the day rather than propagating.
    assert calls["poll"] == sched.ACQUIRE_MAX_ATTEMPTS
    assert calls["fail"] == 1


@pytest.mark.asyncio
async def test_non_polled_module_runs_once_without_retry(monkeypatch):
    calls = {"poll": 0, "run": 0, "fail": 0}

    async def plan_no_retry(_module_id):
        return (False, "src1")

    async def fake_poll(_source_id):
        calls["poll"] += 1
        return (0, 0)

    async def fake_run(_module_id, _name):
        calls["run"] += 1

    async def fake_fail(_module_id, _name):
        calls["fail"] += 1

    monkeypatch.setattr(sched, "_acquire_plan", plan_no_retry)
    monkeypatch.setattr(sched, "poll_source", fake_poll)
    monkeypatch.setattr(sched, "_do_module_run", fake_run)
    monkeypatch.setattr(sched, "_record_failed_day", fake_fail)

    await sched._run_module_job(1, "m")
    assert calls == {"poll": 0, "run": 1, "fail": 0}


@pytest.mark.asyncio
async def test_missing_module_is_a_noop(monkeypatch):
    async def gone(_module_id):
        return None

    ran = {"n": 0}

    async def fake_run(_module_id, _name):
        ran["n"] += 1

    monkeypatch.setattr(sched, "_acquire_plan", gone)
    monkeypatch.setattr(sched, "_do_module_run", fake_run)
    await sched._run_module_job(1, "m")
    assert ran["n"] == 0


# --- DB-backed helpers ------------------------------------------------------

from datetime import datetime, timezone  # noqa: E402


async def _seed_source(session, name, adapter, enabled=True):
    from megatron.core.models import SourceConfig

    session.add(
        SourceConfig(
            name=name, source_type="test", adapter=adapter, enabled=enabled, kind="test"
        )
    )
    await session.commit()


async def _seed_module(session, name, source, filter_config):
    from megatron.core.engine_models import AnalysisModule, LLMProvider, PromptTemplate

    prompt = PromptTemplate(name=f"p_{name}", template="x")
    provider = LLMProvider(name=f"llm_{name}", model="m")
    session.add_all([prompt, provider])
    await session.commit()
    await session.refresh(prompt)
    await session.refresh(provider)

    m = AnalysisModule(
        name=name,
        source=source,
        filter_config=filter_config,
        prompt_template_id=prompt.id,
        provider_id=provider.id,
        schedule_cron="0 1 * * *",
        enabled=True,
    )
    session.add(m)
    await session.commit()
    await session.refresh(m)
    return m.id


async def _add_item(session, source, collect_date):
    from megatron.core.models import ItemRecord

    now = datetime.now(timezone.utc)
    session.add(
        ItemRecord(
            item_id=f"{source}-{collect_date}-x",
            source=source,
            collect_date=collect_date,
            published_at=now,
            collected_at=now,
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_today_present_reflects_ingested_rows():
    from megatron.core.db import async_session_factory

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with async_session_factory() as s:
        await _seed_source(s, "src_tp", "bundle_pull")
        mid = await _seed_module(s, "mod_tp", "src_tp", {"time_mode": "today"})

    assert await sched._today_present(mid) is False

    async with async_session_factory() as s:
        await _add_item(s, "src_tp", "2000-01-01")  # some other day
    assert await sched._today_present(mid) is False

    async with async_session_factory() as s:
        await _add_item(s, "src_tp", today)  # today's data lands
    assert await sched._today_present(mid) is True


@pytest.mark.asyncio
async def test_acquire_plan_retries_only_for_polled_today_source():
    from megatron.core.db import async_session_factory

    async with async_session_factory() as s:
        await _seed_source(s, "src_poll", "bundle_pull")
        await _seed_source(s, "src_native", "native")
        polled_today = await _seed_module(s, "m_pt", "src_poll", {"time_mode": "today"})
        polled_date = await _seed_module(s, "m_pd", "src_poll", {"time_mode": "date"})
        native_today = await _seed_module(s, "m_nt", "src_native", {"time_mode": "today"})

    assert await sched._acquire_plan(polled_today) == (True, "src_poll")
    assert await sched._acquire_plan(polled_date) == (False, "src_poll")
    assert await sched._acquire_plan(native_today) == (False, "src_native")
    assert await sched._acquire_plan(999999) is None
