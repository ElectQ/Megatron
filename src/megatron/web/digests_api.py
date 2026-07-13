"""Admin API for webhook message templates (the `digest_style` a task selects).

DB-backed and editable, seeded from config/digests/*.md. Preview renders the
template against a synthetic bundle so an editor sees the shape without a run.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.db import get_session
from ..core.security import admin_auth

router = APIRouter(prefix="/api/admin/digests", tags=["digests"])


class DigestOut(BaseModel):
    id: int
    style: str
    display_name: str
    body: str
    is_active: bool
    used_by: list[str] = []


class DigestIn(BaseModel):
    display_name: str = ""
    body: str
    is_active: bool = True


def _sample_bundle(style: str) -> dict:
    """A synthetic day bundle so a template renders in the preview."""

    def item(n, tier):
        return {
            "id": n,
            "tier": tier,
            "one_liner": f"示例条目 {n}：某漏洞/工具被披露",
            "why_for_me": "和你关注的自托管 Agent 直接相关",
            "url": f"https://example.com/{n}",
        }

    items = [item(i, "must_see_push") for i in range(1, 4)] + [
        item(i, "recommend") for i in range(4, 9)
    ]
    push_ids = [str(i["id"]) for i in items]
    return {
        "title": "示例源",
        "date": "2026-07-12",
        "digest_style": style,
        "stats": {"ingest_total": 144},
        "day_url": "https://megatron.example.com/day/example/2026-07-12?k=demo",
        "public_url": "https://megatron.example.com/zh/example/2026-07-12",
        "items": items,
        "push_item_ids": push_ids,
    }


async def _used_by(session: AsyncSession, style: str) -> list[str]:
    """Which analysis tasks push with this style."""
    from ..core.engine_models import AnalysisModule

    mods = (await session.execute(select(AnalysisModule))).scalars().all()
    out = []
    for m in mods:
        fc = m.filter_config or {}
        s = fc.get("digest_style", "digest") if fc.get("output_mode") == "day_bundle" else None
        if s == style:
            out.append(m.name)
    return out


@router.get("", response_model=list[DigestOut], dependencies=[Depends(admin_auth)])
async def list_digests(session: AsyncSession = Depends(get_session)):
    from ..core.engine_models import DigestTemplate

    rows = (
        (await session.execute(select(DigestTemplate).order_by(DigestTemplate.style)))
        .scalars()
        .all()
    )
    return [
        DigestOut(
            id=r.id,
            style=r.style,
            display_name=r.display_name or r.style,
            body=r.body,
            is_active=r.is_active,
            used_by=await _used_by(session, r.style),
        )
        for r in rows
    ]


@router.put("/{style}", response_model=DigestOut, dependencies=[Depends(admin_auth)])
async def update_digest(style: str, body: DigestIn, session: AsyncSession = Depends(get_session)):
    from ..core.engine_models import DigestTemplate

    row = (
        (await session.execute(select(DigestTemplate).where(DigestTemplate.style == style)))
        .scalars()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown digest style")
    _check_renders(body.body)
    row.body = body.body
    row.display_name = body.display_name or row.display_name
    row.is_active = body.is_active
    await session.commit()
    await session.refresh(row)
    return DigestOut(
        id=row.id,
        style=row.style,
        display_name=row.display_name or row.style,
        body=row.body,
        is_active=row.is_active,
        used_by=await _used_by(session, row.style),
    )


@router.post("/preview", dependencies=[Depends(admin_auth)])
async def preview_digest(
    body: str = Body(..., embed=True), style: str = Body("digest", embed=True)
):
    """Render a candidate template against a synthetic bundle."""
    from ..engine.doorbell import render_digest

    try:
        rendered = render_digest(_sample_bundle(style), body=body)
    except Exception as e:  # a template typo is a 200 with the error, not a 500
        return {"rendered": f"⚠️ 模板渲染错误：{e}", "ok": False}
    return {"rendered": rendered, "ok": True}


def _check_renders(template_body: str) -> None:
    from ..engine.doorbell import render_digest

    try:
        render_digest(_sample_bundle("digest"), body=template_body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"模板无法渲染：{e}") from e


__all__ = ["router"]
