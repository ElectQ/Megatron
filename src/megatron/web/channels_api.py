from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.db import get_session
from ..core.security import admin_auth, decrypt_config, encrypt_config, mask_config
from ..plugins.webhooks.base import channel_registry

router = APIRouter(prefix="/api/admin/channels", tags=["channels"])


class ChannelIn(BaseModel):
    name: str
    kind: str
    config: dict = {}
    enabled: bool = True


class ChannelOut(BaseModel):
    id: int
    name: str
    kind: str
    config_masked: dict
    enabled: bool


def _to_out(ch) -> ChannelOut:
    return ChannelOut(
        id=ch.id,
        name=ch.name,
        kind=ch.kind,
        config_masked=mask_config(ch.config or {}),
        enabled=ch.enabled,
    )


@router.get("", response_model=list[ChannelOut], dependencies=[Depends(admin_auth)])
async def list_channels(session: AsyncSession = Depends(get_session)):
    from ..core.engine_models import WebhookChannel

    result = await session.execute(select(WebhookChannel).order_by(WebhookChannel.id))
    return [_to_out(ch) for ch in result.scalars().all()]


@router.post("", response_model=ChannelOut, status_code=201, dependencies=[Depends(admin_auth)])
async def create_channel(body: ChannelIn, session: AsyncSession = Depends(get_session)):
    from ..core.engine_models import WebhookChannel

    if body.kind not in channel_registry:
        raise HTTPException(
            400,
            f"Unknown channel kind '{body.kind}'. Available: {channel_registry.names()}",
        )
    ch = WebhookChannel(
        name=body.name,
        kind=body.kind,
        config=encrypt_config(body.config),
        enabled=body.enabled,
    )
    session.add(ch)
    await session.commit()
    await session.refresh(ch)
    return _to_out(ch)


@router.delete("/{cid}", dependencies=[Depends(admin_auth)])
async def delete_channel(cid: int, session: AsyncSession = Depends(get_session)):
    from ..core.engine_models import AnalysisModule, ModuleChannel, WebhookChannel

    ch = await session.get(WebhookChannel, cid)
    if not ch:
        raise HTTPException(404, "Channel not found")
    linked_module_id = (
        (
            await session.execute(
                select(ModuleChannel.module_id).where(ModuleChannel.channel_id == cid)
            )
        )
        .scalars()
        .first()
    )
    if linked_module_id:
        raise HTTPException(409, f"Channel is used by module #{linked_module_id}")

    legacy_modules = (
        await session.execute(select(AnalysisModule.id, AnalysisModule.webhook_channel_ids))
    ).all()
    for module_id, channel_ids in legacy_modules:
        normalized = []
        for value in channel_ids or []:
            try:
                normalized.append(int(value))
            except (TypeError, ValueError):
                continue
        if cid in normalized:
            raise HTTPException(409, f"Channel is used by module #{module_id}")
    await session.delete(ch)
    await session.commit()
    return {"deleted": cid}


@router.post("/{cid}/test", dependencies=[Depends(admin_auth)])
async def test_channel(cid: int, session: AsyncSession = Depends(get_session)):
    from ..core.engine_models import WebhookChannel

    ch = await session.get(WebhookChannel, cid)
    if not ch:
        raise HTTPException(404, "Channel not found")
    if ch.kind not in channel_registry:
        return {"ok": False, "error": f"Unknown kind '{ch.kind}'"}
    channel = channel_registry.create(ch.kind, **decrypt_config(ch.config or {}))
    return await channel.test()


@router.get("/options", dependencies=[Depends(admin_auth)])
async def channel_options():
    return {"kinds": channel_registry.names()}


__all__ = ["router"]
