from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.db import get_session
from ..core.security import admin_auth
from ..engine.agent import agent_registry
from ..plugins.filters.base import filter_registry
from ..plugins.sources.base import source_registry
from ..plugins.tools.base import tool_registry

router = APIRouter(prefix="/api/admin/modules", tags=["modules"])


async def _execute_run_background(run_id: int) -> None:
    from ..core.db import async_session_factory
    from ..core.logging import get_logger
    from ..engine.runner import ModuleRunner

    logger = get_logger(__name__)
    async with async_session_factory() as session:
        runner = ModuleRunner(session)
        try:
            await runner.run_run(run_id)
        except Exception as e:
            logger.error("module.background_run_failed", run_id=run_id, error=str(e))


class ModuleIn(BaseModel):
    name: str
    description: str = ""
    source: str = "twitter"
    source_ref: str = ""
    filter_config: dict = {}
    prompt_template_id: int
    provider_id: int
    agent_backend: str = "none"
    tools_config: list = []
    webhook_channel_ids: list = []
    schedule_cron: str = ""
    enabled: bool = True


class ModuleOut(BaseModel):
    id: int
    name: str
    description: str
    source: str
    source_ref: str
    filter_config: dict
    prompt_template_id: int
    provider_id: int
    agent_backend: str
    tools_config: list
    webhook_channel_ids: list
    schedule_cron: str
    enabled: bool


async def _module_channel_ids(session: AsyncSession, module_id: int) -> list[int]:
    from ..core.engine_models import ModuleChannel

    rows = (
        await session.execute(
            select(ModuleChannel.channel_id)
            .where(ModuleChannel.module_id == module_id)
            .order_by(ModuleChannel.position, ModuleChannel.channel_id)
        )
    ).scalars().all()
    return [int(r) for r in rows]


async def _to_out(session: AsyncSession, m) -> ModuleOut:
    channel_ids = await _module_channel_ids(session, m.id)
    if not channel_ids:
        channel_ids = list(m.webhook_channel_ids or [])
    return ModuleOut(
        id=m.id,
        name=m.name,
        description=m.description,
        source=m.source,
        source_ref=m.source_ref,
        filter_config=m.filter_config or {},
        prompt_template_id=m.prompt_template_id,
        provider_id=m.provider_id,
        agent_backend=m.agent_backend,
        tools_config=m.tools_config or [],
        webhook_channel_ids=channel_ids,
        schedule_cron=m.schedule_cron,
        enabled=m.enabled,
    )


async def _validate_channel_ids(session: AsyncSession, channel_ids: list) -> list[int]:
    from ..core.engine_models import WebhookChannel

    normalized = []
    seen = set()
    for raw in channel_ids or []:
        try:
            cid = int(raw)
        except (TypeError, ValueError):
            raise HTTPException(400, f"Invalid channel id: {raw!r}")
        if cid not in seen:
            seen.add(cid)
            normalized.append(cid)

    if not normalized:
        return []

    existing = set(
        (
            await session.execute(
                select(WebhookChannel.id).where(WebhookChannel.id.in_(normalized))
            )
        ).scalars().all()
    )
    missing = [cid for cid in normalized if cid not in existing]
    if missing:
        raise HTTPException(400, f"Unknown webhook channel ids: {missing}")
    return normalized


async def _sync_module_channels(
    session: AsyncSession,
    module_id: int,
    channel_ids: list[int],
) -> None:
    from ..core.engine_models import ModuleChannel

    await session.execute(delete(ModuleChannel).where(ModuleChannel.module_id == module_id))
    for pos, cid in enumerate(channel_ids):
        session.add(ModuleChannel(module_id=module_id, channel_id=cid, position=pos))


@router.get("", response_model=list[ModuleOut], dependencies=[Depends(admin_auth)])
async def list_modules(session: AsyncSession = Depends(get_session)):
    from ..core.engine_models import AnalysisModule

    result = await session.execute(select(AnalysisModule).order_by(AnalysisModule.id))
    return [await _to_out(session, m) for m in result.scalars().all()]


@router.post("", response_model=ModuleOut, status_code=201, dependencies=[Depends(admin_auth)])
async def create_module(body: ModuleIn, session: AsyncSession = Depends(get_session)):
    from ..core.engine_models import AnalysisModule
    from ..scheduler import reload_module_schedules

    channel_ids = await _validate_channel_ids(session, body.webhook_channel_ids)
    m = AnalysisModule(
        name=body.name,
        description=body.description,
        source=body.source,
        source_ref=body.source_ref,
        filter_config=body.filter_config,
        prompt_template_id=body.prompt_template_id,
        provider_id=body.provider_id,
        agent_backend=body.agent_backend,
        tools_config=body.tools_config,
        webhook_channel_ids=channel_ids,
        schedule_cron=body.schedule_cron,
        enabled=body.enabled,
    )
    session.add(m)
    await session.flush()
    await _sync_module_channels(session, m.id, channel_ids)
    await session.commit()
    await session.refresh(m)
    if body.schedule_cron:
        await reload_module_schedules()
    return await _to_out(session, m)


@router.put("/{module_id}", response_model=ModuleOut, dependencies=[Depends(admin_auth)])
async def update_module(
    module_id: int,
    body: ModuleIn,
    session: AsyncSession = Depends(get_session),
):
    from ..core.engine_models import AnalysisModule
    from ..scheduler import reload_module_schedules

    m = await session.get(AnalysisModule, module_id)
    if not m:
        raise HTTPException(404, "Module not found")
    channel_ids = await _validate_channel_ids(session, body.webhook_channel_ids)
    m.name = body.name
    m.description = body.description
    m.source = body.source
    m.source_ref = body.source_ref
    m.filter_config = body.filter_config
    m.prompt_template_id = body.prompt_template_id
    m.provider_id = body.provider_id
    m.agent_backend = body.agent_backend
    m.tools_config = body.tools_config
    m.webhook_channel_ids = channel_ids
    m.schedule_cron = body.schedule_cron
    m.enabled = body.enabled
    await _sync_module_channels(session, m.id, channel_ids)
    await session.commit()
    await session.refresh(m)
    await reload_module_schedules()
    return await _to_out(session, m)


@router.delete("/{module_id}", dependencies=[Depends(admin_auth)])
async def delete_module(module_id: int, session: AsyncSession = Depends(get_session)):
    from ..core.engine_models import AnalysisModule, ModuleChannel
    from ..scheduler import reload_module_schedules

    m = await session.get(AnalysisModule, module_id)
    if not m:
        raise HTTPException(404, "Module not found")
    had_cron = bool(m.schedule_cron)
    await session.execute(delete(ModuleChannel).where(ModuleChannel.module_id == module_id))
    await session.delete(m)
    await session.commit()
    if had_cron:
        await reload_module_schedules()
    return {"deleted": module_id}


@router.post("/{module_id}/run", status_code=202, dependencies=[Depends(admin_auth)])
async def run_module(
    module_id: int,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    from ..engine.runner import ActiveRunExists, ModuleRunner

    runner = ModuleRunner(session)
    try:
        summary = await runner.create_run(module_id, triggered_by="manual")
        background_tasks.add_task(_execute_run_background, summary["run_id"])
        return summary
    except ActiveRunExists as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/options", dependencies=[Depends(admin_auth)])
async def get_options():
    """List all available plugins for the module editor."""
    return {
        "sources": source_registry.names(),
        "filters": filter_registry.names(),
        "tools": tool_registry.names(),
        "agents": ["none"] + agent_registry.names(),
    }


__all__ = ["router"]
