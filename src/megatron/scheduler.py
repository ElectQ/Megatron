from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from .config import ingest_settings
from .core.db import async_session_factory
from .core.logging import get_logger
from .ingest.puller import GitPuller

logger = get_logger(__name__)

_scheduler: AsyncIOScheduler | None = None

MODULE_JOB_PREFIX = "module_"


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="UTC")
    return _scheduler


def _setup_pull_job(scheduler: AsyncIOScheduler) -> None:
    if not ingest_settings.soundwave_repo_url:
        logger.warning("scheduler.no_pull_repo", reason="SOUNDWAVE_REPO_URL unset")
        return

    async def _pull_job():
        puller = GitPuller(
            ingest_settings.soundwave_repo_url,
            source="twitter",
            mode="auto",
        )
        try:
            ingested, duplicated, dates = await puller.run()
            logger.info(
                "scheduler.pull.done",
                ingested=ingested,
                duplicated=duplicated,
                dates=dates,
            )
        except Exception as e:
            logger.error("scheduler.pull.failed", error=str(e))

    scheduler.add_job(
        _pull_job,
        CronTrigger(hour=6, minute=0),
        id="soundwave_pull",
        replace_existing=True,
    )
    logger.info("scheduler.pull.registered", cron="daily 06:00 UTC")


async def _run_module_job(module_id: int, module_name: str) -> None:
    """Background task executed by APScheduler for a scheduled module."""
    from .engine.runner import ModuleRunner

    try:
        async with async_session_factory() as session:
            runner = ModuleRunner(session)
            summary = await runner.run_module(module_id, triggered_by="schedule")
        logger.info(
            "scheduler.module.done",
            module=module_name,
            module_id=module_id,
            run_id=summary.get("run_id"),
            status=summary.get("status"),
        )
    except Exception as e:
        logger.error(
            "scheduler.module.failed",
            module=module_name,
            module_id=module_id,
            error=str(e),
        )


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
    _setup_pull_job(scheduler)
    scheduler.start()
    loop = asyncio.get_event_loop()
    if loop.is_running():
        loop.create_task(_load_module_schedules(scheduler))
    else:
        asyncio.run(_load_module_schedules(scheduler))
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
    "list_schedules",
]
