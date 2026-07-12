"""Admin API for the global filtering policy (caps + politics blocklist).

DB-backed, seeded from config/policy.yaml. A single row; GET returns it (seeding
it lazily from the file if the table is somehow empty), PUT replaces it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.db import get_session
from ..core.security import admin_auth

router = APIRouter(prefix="/api/admin/policy", tags=["policy"])


class PolicyBody(BaseModel):
    caps: dict = {}
    politics_blocklist: list[str] = []


async def _row(session: AsyncSession):
    from ..core.engine_models import Policy
    from ..profile.policy import load_policy

    row = (await session.execute(select(Policy))).scalars().first()
    if row is None:
        pol = load_policy()
        row = Policy(caps=pol["caps"], politics_blocklist=pol["politics_blocklist"])
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


@router.get("", dependencies=[Depends(admin_auth)])
async def get_policy(session: AsyncSession = Depends(get_session)):
    row = await _row(session)
    return {"caps": row.caps or {}, "politics_blocklist": row.politics_blocklist or []}


@router.put("", dependencies=[Depends(admin_auth)])
async def update_policy(body: PolicyBody, session: AsyncSession = Depends(get_session)):
    from datetime import datetime, timezone

    row = await _row(session)
    # Keep only the known cap keys, coerced to int, so a typo can't inject junk.
    known = ("lead_min", "must_see_min", "must_see_max", "recommend_max", "skim_max")
    row.caps = {k: int(body.caps.get(k, (row.caps or {}).get(k, 0)) or 0) for k in known}
    row.politics_blocklist = [t.strip() for t in body.politics_blocklist if t and t.strip()]
    row.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return {"caps": row.caps, "politics_blocklist": row.politics_blocklist}


__all__ = ["router"]
