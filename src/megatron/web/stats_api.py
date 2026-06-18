from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import Date, Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.db import get_session
from ..core.engine_models import AnalysisModule, AnalysisRun
from ..core.models import ItemRecord
from ..core.security import admin_auth

router = APIRouter(prefix="/api/admin/stats", tags=["stats"])


def _today_start():
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


@router.get("/overview", dependencies=[Depends(admin_auth)])
async def overview(session: AsyncSession = Depends(get_session)):
    """Aggregate run + items statistics for dashboard display."""
    ts = _today_start()
    today_str = ts.strftime("%Y-%m-%d")

    today_total = (
        await session.execute(
            select(func.count(AnalysisRun.id)).where(AnalysisRun.started_at >= ts)
        )
    ).scalar_one()
    today_success = (
        await session.execute(
            select(func.count(AnalysisRun.id)).where(
                AnalysisRun.started_at >= ts,
                AnalysisRun.status == "completed",
            )
        )
    ).scalar_one()
    today_tokens = (
        await session.execute(
            select(func.sum(AnalysisRun.prompt_tokens + AnalysisRun.completion_tokens)).where(
                AnalysisRun.started_at >= ts
            )
        )
    ).scalar_one()
    today_cost = (
        await session.execute(
            select(func.sum(AnalysisRun.total_cost_usd)).where(AnalysisRun.started_at >= ts)
        )
    ).scalar_one()
    today_duration = (
        await session.execute(
            select(func.sum(AnalysisRun.duration_sec)).where(AnalysisRun.started_at >= ts)
        )
    ).scalar_one()
    today_new_items = (
        await session.execute(
            select(func.count(ItemRecord.id)).where(ItemRecord.collect_date == today_str)
        )
    ).scalar_one()

    all_total = (await session.execute(select(func.count(AnalysisRun.id)))).scalar_one()
    all_tokens = (
        await session.execute(
            select(func.sum(AnalysisRun.prompt_tokens + AnalysisRun.completion_tokens))
        )
    ).scalar_one()
    all_cost = (await session.execute(select(func.sum(AnalysisRun.total_cost_usd)))).scalar_one()
    all_items = (await session.execute(select(func.count(ItemRecord.id)))).scalar_one()

    return {
        "today": {
            "total": today_total,
            "success": today_success,
            "failed": today_total - today_success,
            "success_rate": round(today_success / today_total * 100, 1) if today_total else 0,
            "tokens": int(today_tokens or 0),
            "cost_usd": round(float(today_cost or 0), 6),
            "duration_sec": round(float(today_duration or 0), 2),
            "new_items": today_new_items,
        },
        "all_time": {
            "total": all_total,
            "tokens": int(all_tokens or 0),
            "cost_usd": round(float(all_cost or 0), 6),
            "items": all_items,
        },
    }


@router.get("/trend", dependencies=[Depends(admin_auth)])
async def trend(days: int = 7, session: AsyncSession = Depends(get_session)):
    """N-day trend of runs + tokens + cost (UTC, oldest first)."""
    days = max(1, min(days, 30))
    start = (datetime.now(timezone.utc) - timedelta(days=days - 1)).strftime("%Y-%m-%d")

    stmt = (
        select(
            cast(AnalysisRun.started_at, Date).label("d"),
            func.count(AnalysisRun.id).label("runs"),
            func.sum(AnalysisRun.prompt_tokens + AnalysisRun.completion_tokens).label("tokens"),
            func.sum(AnalysisRun.total_cost_usd).label("cost"),
        )
        .where(cast(AnalysisRun.started_at, Date) >= start)
        .group_by("d")
        .order_by("d")
    )
    rows = (await session.execute(stmt)).all()
    by_date = {str(r.d): r for r in rows}

    out = []
    for i in range(days):
        d = (datetime.now(timezone.utc) - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        r = by_date.get(d)
        out.append(
            {
                "date": d,
                "runs": int(r.runs) if r else 0,
                "tokens": int(r.tokens) if r and r.tokens else 0,
                "cost_usd": round(float(r.cost) if r and r.cost else 0.0, 6),
            }
        )
    return out


@router.get("/per-module", dependencies=[Depends(admin_auth)])
async def per_module(session: AsyncSession = Depends(get_session)):
    """Per-module execution statistics."""
    rows = (
        await session.execute(
            select(
                AnalysisRun.module_id,
                func.count(AnalysisRun.id).label("runs"),
                func.sum((AnalysisRun.status == "completed").cast(Integer)).label("success"),
                func.sum(AnalysisRun.prompt_tokens + AnalysisRun.completion_tokens).label("tokens"),
                func.sum(AnalysisRun.total_cost_usd).label("cost"),
                func.sum(AnalysisRun.duration_sec).label("duration"),
                func.max(AnalysisRun.started_at).label("last_run"),
                func.avg(AnalysisRun.duration_sec).label("avg_duration"),
                func.avg(AnalysisRun.prompt_tokens + AnalysisRun.completion_tokens).label(
                    "avg_tokens"
                ),
            ).group_by(AnalysisRun.module_id)
        )
    ).all()

    module_ids = [r.module_id for r in rows]
    names: dict[int, str] = {}
    if module_ids:
        mod_rows = (
            await session.execute(
                select(AnalysisModule.id, AnalysisModule.name).where(
                    AnalysisModule.id.in_(module_ids)
                )
            )
        ).all()
        names = {m.id: m.name for m in mod_rows}

    return [
        {
            "module_id": r.module_id,
            "module_name": names.get(r.module_id, f"#{r.module_id}"),
            "runs": r.runs,
            "success": int(r.success or 0),
            "success_rate": round((r.success or 0) / r.runs * 100, 1) if r.runs else 0,
            "tokens": int(r.tokens or 0),
            "cost_usd": round(float(r.cost or 0), 6),
            "duration_sec": round(float(r.duration or 0), 2),
            "avg_duration_sec": round(float(r.avg_duration or 0), 2),
            "avg_tokens": int(r.avg_tokens or 0),
            "last_run": r.last_run.isoformat() if r.last_run else None,
        }
        for r in rows
    ]


__all__ = ["router"]
