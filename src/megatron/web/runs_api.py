from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.db import get_session
from ..core.engine_models import AnalysisModule, AnalysisRun
from ..core.security import admin_auth

router = APIRouter(prefix="/api/admin/runs", tags=["runs"])


class RunOut(BaseModel):
    id: int
    module_id: int
    module_name: str
    status: str
    input_count: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    duration_sec: float
    tool_calls: list
    module_snapshot: dict
    prompt_snapshot: dict
    provider_snapshot: dict
    rendered_prompt_hash: str
    triggered_by: str
    started_at: datetime
    finished_at: datetime | None
    result: dict
    error: str


async def _build_name_map(session: AsyncSession, module_ids: list[int]) -> dict[int, str]:
    if not module_ids:
        return {}
    rows = (
        await session.execute(
            select(AnalysisModule.id, AnalysisModule.name).where(AnalysisModule.id.in_(module_ids))
        )
    ).all()
    return {r.id: r.name for r in rows}


@router.get("", response_model=list[RunOut], dependencies=[Depends(admin_auth)])
async def list_runs(
    module_id: int | None = None,
    status: str | None = None,
    date: str | None = None,
    limit: int = 20,
    session: AsyncSession = Depends(get_session),
):
    stmt = select(AnalysisRun).order_by(AnalysisRun.id.desc()).limit(limit)
    if module_id:
        stmt = stmt.where(AnalysisRun.module_id == module_id)
    if status:
        stmt = stmt.where(AnalysisRun.status == status)
    if date:
        stmt = stmt.where(cast(AnalysisRun.started_at, Date) == func.date(date))

    result = await session.execute(stmt)
    runs = result.scalars().all()
    name_map = await _build_name_map(session, [r.module_id for r in runs])
    return [_to_out(r, name_map.get(r.module_id, f"#{r.module_id}")) for r in runs]


@router.get("/{run_id}", response_model=RunOut, dependencies=[Depends(admin_auth)])
async def get_run(run_id: int, session: AsyncSession = Depends(get_session)):
    r = await session.get(AnalysisRun, run_id)
    if not r:
        raise HTTPException(404, "Run not found")
    name_map = await _build_name_map(session, [r.module_id])
    return _to_out(r, name_map.get(r.module_id, f"#{r.module_id}"))


def _to_out(r, module_name: str = "") -> RunOut:
    return RunOut(
        id=r.id,
        module_id=r.module_id,
        module_name=module_name,
        status=r.status,
        input_count=r.input_count,
        prompt_tokens=r.prompt_tokens,
        completion_tokens=r.completion_tokens,
        cost_usd=r.total_cost_usd,
        duration_sec=r.duration_sec,
        tool_calls=r.tool_calls or [],
        module_snapshot=r.module_snapshot or {},
        prompt_snapshot=r.prompt_snapshot or {},
        provider_snapshot=r.provider_snapshot or {},
        rendered_prompt_hash=r.rendered_prompt_hash or "",
        triggered_by=r.triggered_by,
        started_at=r.started_at,
        finished_at=r.finished_at,
        result=r.result or {},
        error=r.error or "",
    )


__all__ = ["router"]
