"""Admin API for system settings (the domain / base_url).

DB-backed so an operator sets the public domain in the UI, once, without touching
env or redeploying. Seeded from MEGATRON_BASE_URL at first boot.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.db import get_session
from ..core.security import admin_auth
from ..core.sysconfig import is_local_base_url

router = APIRouter(prefix="/api/admin/settings", tags=["settings"])


class SettingsBody(BaseModel):
    base_url: str = ""


async def _row(session: AsyncSession):
    from ..config import settings
    from ..core.engine_models import SystemSetting

    row = (await session.execute(select(SystemSetting))).scalars().first()
    if row is None:
        row = SystemSetting(base_url=settings.base_url or "")
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


@router.get("", dependencies=[Depends(admin_auth)])
async def get_settings(session: AsyncSession = Depends(get_session)):
    row = await _row(session)
    return {
        "base_url": row.base_url or "",
        "base_url_is_local": is_local_base_url(row.base_url or ""),
    }


@router.put("", dependencies=[Depends(admin_auth)])
async def update_settings(body: SettingsBody, session: AsyncSession = Depends(get_session)):
    from datetime import datetime, timezone

    row = await _row(session)
    row.base_url = (body.base_url or "").strip().rstrip("/")
    row.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return {"base_url": row.base_url, "base_url_is_local": is_local_base_url(row.base_url)}


__all__ = ["router"]
