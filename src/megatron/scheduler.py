from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select

from .core.db import async_session_factory
from .core.logging import get_logger

logger = get_logger(__name__)

_scheduler: AsyncIOScheduler | None = None

MODULE_JOB_PREFIX = "module_"
PULL_JOB_PREFIX = "pull_"

DEFAULT_PULL_CRON = "0 6 * * *"

# Acquire-with-retry (§ scheduled analysis of a polled source).
# A scheduled task for a polled 'today' source may fire before the day's upstream
# bundle has landed (GitHub Actions cron can lag hours). Instead of analysing an
# empty day, the job re-pulls and waits: starting at the task's scheduled time
# (北京 09:00 = 01:00 UTC), it tries ACQUIRE_MAX_ATTEMPTS times, one hour apart.
# The first attempt that sees today's data analyses + pushes; if the day never
# arrives, a failed run is recorded and no push goes out (当天失败).
ACQUIRE_MAX_ATTEMPTS = 5
ACQUIRE_RETRY_SECONDS = 3600


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="UTC")
    return _scheduler


async def poll_source(source_id: str) -> tuple[int, int]:
    """Fetch one polled source and ingest whatever is new. Returns (ingested, duplicated).

    Incremental via the pull_state watermark, and idempotent regardless: the
    dedup key is (source_id, external_id), so a re-poll of a day we already have
    costs one HTTP request and inserts nothing.
    """
    from datetime import datetime, timedelta

    from .ingest.puller import GitPuller
    from .ingest.registry import get_source
    from .ingest.service import IngestService
    from .ingest.watermark import advance_watermark, get_watermark
    from .plugins.sources.base import source_registry

    async with async_session_factory() as session:
        sc = await get_source(session, source_id)
        if sc is None or not sc.enabled:
            logger.warning("scheduler.pull.source_gone", source=source_id)
            return 0, 0

        cfg = dict(sc.config or {})

        # git_pull keeps its own puller: it clones a repo rather than fetching a URL.
        if sc.adapter == "git_pull":
            puller = GitPuller(
                cfg.get("repo_url", ""),
                source=sc.name,
                plugin=cfg.get("plugin_name", "twitter"),
                mode="auto",
            )
            ingested, duplicated, dates = await puller.run()
            logger.info(
                "scheduler.pull.done",
                source=source_id,
                adapter=sc.adapter,
                ingested=ingested,
                duplicated=duplicated,
                dates=dates,
            )
            return ingested, duplicated

        kind = sc.adapter  # http_pull / bundle_pull are also the plugin names
        if kind not in source_registry:
            logger.error("scheduler.pull.no_plugin", source=source_id, adapter=sc.adapter)
            return 0, 0

        watermark = await get_watermark(session, sc.name)
        since = None
        if watermark:
            since = datetime.strptime(watermark, "%Y-%m-%d") + timedelta(days=1)

        plugin = source_registry.create(kind, source_label=sc.name, **cfg)
        try:
            items = await plugin.fetch(since=since)
        finally:
            await plugin.close()

        if not items:
            logger.info("scheduler.pull.no_items", source=source_id, since=watermark)
            return 0, 0

        service = IngestService(session)
        latest = max(it.collect_date for it in items if it.collect_date)
        ingested, duplicated = await service.ingest_items(items, mode="pull", date=latest)
        if latest:
            await advance_watermark(session, sc.name, latest)
            await session.commit()

    logger.info(
        "scheduler.pull.done",
        source=source_id,
        adapter=kind,
        ingested=ingested,
        duplicated=duplicated,
        latest_date=latest,
    )
    return ingested, duplicated


async def _pull_job(source_id: str) -> None:
    try:
        await poll_source(source_id)
    except Exception as e:
        # A collector being down must never take the scheduler with it; the
        # analysis run will report the source as missing and still publish.
        logger.error("scheduler.pull.failed", source=source_id, error=str(e))


async def _load_pull_jobs(scheduler: AsyncIOScheduler) -> int:
    """One job per enabled source Megatron polls itself. Idempotent."""
    from .ingest.registry import list_sources
    from .ingest.spec import POLLED_ADAPTERS

    for job in scheduler.get_jobs():
        if job.id.startswith(PULL_JOB_PREFIX):
            scheduler.remove_job(job.id)

    count = 0
    async with async_session_factory() as session:
        for sc in await list_sources(session, enabled_only=True):
            if sc.adapter not in POLLED_ADAPTERS:
                continue  # http_push sources come to us
            cron = (sc.config or {}).get("cron") or DEFAULT_PULL_CRON
            try:
                trigger = CronTrigger.from_crontab(cron)
            except Exception as e:
                logger.warning("scheduler.pull.bad_cron", source=sc.name, cron=cron, error=str(e))
                continue
            scheduler.add_job(
                _pull_job,
                trigger,
                args=[sc.name],
                id=f"{PULL_JOB_PREFIX}{sc.name}",
                name=f"pull:{sc.name}",
                replace_existing=True,
            )
            count += 1
            logger.info(
                "scheduler.pull.registered",
                source=sc.name,
                adapter=sc.adapter,
                cron=cron,
            )

    if not count:
        logger.info("scheduler.no_pull_sources")
    return count


async def reload_pull_jobs() -> int:
    """Re-register pull jobs after the source registry changes."""
    scheduler = get_scheduler()
    if not scheduler.running:
        logger.warning("scheduler.reload.skipped", reason="not running")
        return 0
    return await _load_pull_jobs(scheduler)


async def _run_module_job(module_id: int, module_name: str) -> None:
    """APScheduler entrypoint for a scheduled module.

    For a polled 'today' source, first *acquire* the day's bundle: re-pull and,
    if today's data has not landed yet, wait an hour and retry, up to
    ACQUIRE_MAX_ATTEMPTS times. The first attempt that sees the data analyses +
    pushes; if it never arrives, the day is marked failed. Non-polled or
    non-'today' modules run immediately, exactly as before.
    """
    import asyncio

    plan = await _acquire_plan(module_id)
    if plan is None:
        logger.warning("scheduler.module.gone", module=module_name, module_id=module_id)
        return
    retry, source_id = plan

    if not retry:
        await _do_module_run(module_id, module_name)
        return

    for attempt in range(1, ACQUIRE_MAX_ATTEMPTS + 1):
        try:
            await poll_source(source_id)
        except Exception as e:  # a failed pull is one failed attempt, not a crash
            logger.error(
                "scheduler.acquire.pull_failed",
                module=module_name,
                source=source_id,
                attempt=attempt,
                error=str(e),
            )

        if await _today_present(module_id):
            logger.info(
                "scheduler.acquire.ready",
                module=module_name,
                source=source_id,
                attempt=attempt,
            )
            await _do_module_run(module_id, module_name)
            return

        logger.info(
            "scheduler.acquire.not_ready",
            module=module_name,
            source=source_id,
            attempt=attempt,
            of=ACQUIRE_MAX_ATTEMPTS,
        )
        if attempt < ACQUIRE_MAX_ATTEMPTS:
            await asyncio.sleep(ACQUIRE_RETRY_SECONDS)

    logger.warning(
        "scheduler.acquire.exhausted",
        module=module_name,
        source=source_id,
        attempts=ACQUIRE_MAX_ATTEMPTS,
    )
    await _record_failed_day(module_id, module_name)


async def _do_module_run(module_id: int, module_name: str) -> None:
    """Run one analysis (analyse + push) for a module — the actual work."""
    from .engine.runner import ActiveRunExists, ModuleRunner

    try:
        async with async_session_factory() as session:
            runner = ModuleRunner(session)
            queued = await runner.create_run(module_id, triggered_by="schedule")
            summary = await runner.run_run(queued["run_id"])
        logger.info(
            "scheduler.module.done",
            module=module_name,
            module_id=module_id,
            run_id=summary.get("run_id"),
            status=summary.get("status"),
        )
    except ActiveRunExists as e:
        logger.warning(
            "scheduler.module.skipped_active_run",
            module=module_name,
            module_id=module_id,
            active_run_id=e.run_id,
            active_status=e.status,
        )
    except Exception as e:
        logger.error(
            "scheduler.module.failed",
            module=module_name,
            module_id=module_id,
            error=str(e),
        )


async def _acquire_plan(module_id: int) -> tuple[bool, str] | None:
    """Whether this module should acquire-with-retry, and its source id.

    Retry only makes sense when the module reads a *polled* source (we can
    re-pull it) in the default 'today' window. Returns ``(retry, source_id)``,
    or ``None`` if the module is gone/disabled.
    """
    from .core.engine_models import AnalysisModule
    from .ingest.registry import get_source
    from .ingest.spec import POLLED_ADAPTERS

    async with async_session_factory() as session:
        module = await session.get(AnalysisModule, module_id)
        if module is None or not module.enabled:
            return None
        time_mode = (module.filter_config or {}).get("time_mode", "today")
        if time_mode not in (None, "today"):
            return (False, module.source)
        sc = await get_source(session, module.source)
        polled = sc is not None and sc.enabled and sc.adapter in POLLED_ADAPTERS
        return (polled, module.source)


async def _today_present(module_id: int) -> bool:
    """True if today's (UTC) data for the module's source(s) is already ingested.

    Mirrors the runner's own 'today' selection exactly — same sources, same
    ``collect_date == today_utc`` — so 'present' here means the analysis will
    actually have rows to work on.
    """
    from datetime import datetime, timezone

    from .core.engine_models import AnalysisModule
    from .core.models import ItemRecord

    async with async_session_factory() as session:
        module = await session.get(AnalysisModule, module_id)
        if module is None:
            return False
        fc = module.filter_config or {}
        sources = fc.get("sources") or [module.source]
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        stmt = (
            select(func.count())
            .select_from(ItemRecord)
            .where(ItemRecord.source.in_(sources), ItemRecord.collect_date == today)
        )
        if module.source_ref:
            stmt = stmt.where(ItemRecord.source_ref == module.source_ref)
        return (await session.execute(stmt)).scalar_one() > 0


async def _record_failed_day(module_id: int, module_name: str) -> None:
    """Record a failed run for a day whose data never arrived — visible in
    Run History, and (having no result) nothing is pushed."""
    from datetime import datetime, timezone

    from .core.engine_models import AnalysisRun

    now = datetime.now(timezone.utc)
    msg = (
        f"当天数据未到达:{ACQUIRE_MAX_ATTEMPTS} 次拉取"
        f"(每 {ACQUIRE_RETRY_SECONDS // 60} 分钟一次)后仍无今日数据,取消当天分析。"
    )
    async with async_session_factory() as session:
        session.add(
            AnalysisRun(
                module_id=module_id,
                status="failed",
                triggered_by="schedule",
                error=msg,
                started_at=now,
                finished_at=now,
            )
        )
        await session.commit()
    logger.warning("scheduler.acquire.day_failed", module=module_name, module_id=module_id)


async def _load_module_schedules(scheduler: AsyncIOScheduler) -> int:
    """Read enabled modules with schedule_cron and register them as jobs.

    Removes any previously-scheduled module jobs first so reload is idempotent.
    Returns the number of modules scheduled.
    """
    from .core.engine_models import AnalysisModule

    for job in scheduler.get_jobs():
        if job.id.startswith(MODULE_JOB_PREFIX):
            scheduler.remove_job(job.id)

    count = 0
    async with async_session_factory() as session:
        stmt = select(AnalysisModule).where(AnalysisModule.enabled.is_(True))
        result = await session.execute(stmt)
        for module in result.scalars().all():
            cron = (module.schedule_cron or "").strip()
            if not cron:
                continue
            try:
                trigger = CronTrigger.from_crontab(cron)
            except Exception as e:
                logger.warning(
                    "scheduler.bad_cron",
                    module=module.name,
                    cron=cron,
                    error=str(e),
                )
                continue
            scheduler.add_job(
                _run_module_job,
                trigger,
                args=[module.id, module.name],
                id=f"{MODULE_JOB_PREFIX}{module.id}",
                name=f"module:{module.name}",
                replace_existing=True,
            )
            count += 1
            logger.info(
                "scheduler.module.registered",
                module=module.name,
                module_id=module.id,
                cron=cron,
            )
    return count


async def reload_module_schedules() -> int:
    """Reload module schedules after module create/update/delete. Idempotent."""
    scheduler = get_scheduler()
    if not scheduler.running:
        logger.warning("scheduler.reload.skipped", reason="not running")
        return 0
    return await _load_module_schedules(scheduler)


def list_schedules() -> list[dict]:
    """Return all scheduled jobs for display."""
    scheduler = get_scheduler()
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append(
            {
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger),
            }
        )
    jobs.sort(key=lambda j: j["next_run"] or "9")
    return jobs


def start_scheduler() -> None:
    import asyncio

    scheduler = get_scheduler()
    scheduler.start()

    async def _load_all():
        await _load_module_schedules(scheduler)
        await _load_pull_jobs(scheduler)

    loop = asyncio.get_event_loop()
    if loop.is_running():
        loop.create_task(_load_all())
    else:
        asyncio.run(_load_all())
    logger.info("scheduler.started")


def shutdown_scheduler() -> None:
    scheduler = get_scheduler()
    if scheduler.running:
        scheduler.shutdown(wait=False)
    logger.info("scheduler.stopped")


__all__ = [
    "get_scheduler",
    "start_scheduler",
    "shutdown_scheduler",
    "reload_module_schedules",
    "reload_pull_jobs",
    "poll_source",
    "list_schedules",
]
