from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.db import get_session
from ..core.security import admin_auth, decrypt_secret, encrypt_secret, mask
from ..llm.provider import LLMProvider

router = APIRouter(prefix="/api/admin/providers", tags=["providers"])


class ProviderIn(BaseModel):
    name: str
    model: str
    api_base: str = ""
    api_key: str = ""
    temperature: float = 0.7
    max_tokens: int = 8192
    enabled: bool = True


class ProviderOut(BaseModel):
    id: int
    name: str
    model: str
    api_base: str
    api_key_masked: str
    temperature: float
    max_tokens: int
    enabled: bool


def _to_out(p) -> ProviderOut:
    api_key = decrypt_secret(p.api_key) if p.api_key else ""
    return ProviderOut(
        id=p.id,
        name=p.name,
        model=p.model,
        api_base=p.api_base,
        api_key_masked=mask(api_key) if api_key else "",
        temperature=p.temperature,
        max_tokens=p.max_tokens,
        enabled=p.enabled,
    )


@router.get("", response_model=list[ProviderOut], dependencies=[Depends(admin_auth)])
async def list_providers(session: AsyncSession = Depends(get_session)):
    from ..core.engine_models import LLMProvider as M

    result = await session.execute(select(M).order_by(M.id))
    return [_to_out(p) for p in result.scalars().all()]


@router.post(
    "",
    response_model=ProviderOut,
    dependencies=[Depends(admin_auth)],
    status_code=201,
)
async def create_provider(body: ProviderIn, session: AsyncSession = Depends(get_session)):
    from ..core.engine_models import LLMProvider as M

    p = M(
        name=body.name,
        model=body.model,
        api_base=body.api_base,
        api_key=encrypt_secret(body.api_key),
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        enabled=body.enabled,
    )
    session.add(p)
    await session.commit()
    await session.refresh(p)
    return _to_out(p)


@router.delete("/{pid}", dependencies=[Depends(admin_auth)])
async def delete_provider(pid: int, session: AsyncSession = Depends(get_session)):
    from ..core.engine_models import AnalysisModule, LLMProvider as M

    p = await session.get(M, pid)
    if not p:
        raise HTTPException(404, "Provider not found")
    used_by = (
        (
            await session.execute(
                select(AnalysisModule.name).where(AnalysisModule.provider_id == pid)
            )
        )
        .scalars()
        .first()
    )
    if used_by:
        raise HTTPException(409, f"Provider is used by module '{used_by}'")
    await session.delete(p)
    await session.commit()
    return {"deleted": pid}


@router.post("/{pid}/test", dependencies=[Depends(admin_auth)])
async def test_provider(pid: int, session: AsyncSession = Depends(get_session)):
    from ..core.engine_models import LLMProvider as M

    p = await session.get(M, pid)
    if not p:
        raise HTTPException(404, "Provider not found")
    try:
        llm = LLMProvider(
            {
                "model": p.model,
                "api_key": decrypt_secret(p.api_key),
                "api_base": p.api_base,
                "temperature": 0.0,
                "max_tokens": 16,
            }
        )
        resp = await llm.chat([{"role": "user", "content": "Say 'OK'."}])
        return {
            "ok": True,
            "model": p.model,
            "reply": resp.content[:60],
            "tokens": resp.prompt_tokens + resp.completion_tokens,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


__all__ = ["router"]
