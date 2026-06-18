from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.db import get_session
from ..core.security import admin_auth
from ..engine.agent import agent_registry
from ..plugins.filters.base import filter_registry
from ..plugins.sources.base import source_registry
from ..plugins.tools.base import tool_registry

router = APIRouter(prefix="/api/admin/modules", tags=["modules"])


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


def _to_out(m) -> ModuleOut:
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
        webhook_channel_ids=m.webhook_channel_ids or [],
        schedule_cron=m.schedule_cron,
        enabled=m.enabled,
    )


@router.get("", response_model=list[ModuleOut], dependencies=[Depends(admin_auth)])
async def list_modules(session: AsyncSession = Depends(get_session)):
    from ..core.engine_models import AnalysisModule

    result = await session.execute(select(AnalysisModule).order_by(AnalysisModule.id))
    return [_to_out(m) for m in result.scalars().all()]


@router.post("", response_model=ModuleOut, status_code=201, dependencies=[Depends(admin_auth)])
async def create_module(body: ModuleIn, session: AsyncSession = Depends(get_session)):
    from ..core.engine_models import AnalysisModule
    from ..scheduler import reload_module_schedules

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
        webhook_channel_ids=body.webhook_channel_ids,
        schedule_cron=body.schedule_cron,
        enabled=body.enabled,
    )
    session.add(m)
    await session.commit()
    await session.refresh(m)
    if body.schedule_cron:
        await reload_module_schedules()
    return _to_out(m)


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
    m.name = body.name
    m.description = body.description
    m.source = body.source
    m.source_ref = body.source_ref
    m.filter_config = body.filter_config
    m.prompt_template_id = body.prompt_template_id
    m.provider_id = body.provider_id
    m.agent_backend = body.agent_backend
    m.tools_config = body.tools_config
    m.webhook_channel_ids = body.webhook_channel_ids
    m.schedule_cron = body.schedule_cron
    m.enabled = body.enabled
    await session.commit()
    await session.refresh(m)
    await reload_module_schedules()
    return _to_out(m)


@router.delete("/{module_id}", dependencies=[Depends(admin_auth)])
async def delete_module(module_id: int, session: AsyncSession = Depends(get_session)):
    from ..core.engine_models import AnalysisModule
    from ..scheduler import reload_module_schedules

    m = await session.get(AnalysisModule, module_id)
    if not m:
        raise HTTPException(404, "Module not found")
    had_cron = bool(m.schedule_cron)
    await session.delete(m)
    await session.commit()
    if had_cron:
        await reload_module_schedules()
    return {"deleted": module_id}


@router.post("/{module_id}/run", dependencies=[Depends(admin_auth)])
async def run_module(
    module_id: int,
    session: AsyncSession = Depends(get_session),
):
    from ..engine.runner import ModuleRunner

    runner = ModuleRunner(session)
    try:
        summary = await runner.run_module(module_id, triggered_by="manual")
        return summary
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
