from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.db import get_session
from ..core.security import admin_auth
from ..engine.template import preview_template

router = APIRouter(prefix="/api/admin/prompts", tags=["prompts"])


class PromptIn(BaseModel):
    name: str
    display_name: str = ""
    template: str
    output_schema: dict = {}
    is_active: bool = True


class PromptOut(BaseModel):
    id: int
    name: str
    display_name: str
    version: int
    template: str
    output_schema: dict
    is_active: bool


@router.get("", response_model=list[PromptOut], dependencies=[Depends(admin_auth)])
async def list_prompts(session: AsyncSession = Depends(get_session)):
    from ..core.engine_models import PromptTemplate

    result = await session.execute(select(PromptTemplate).order_by(PromptTemplate.id))
    return [
        PromptOut(
            id=p.id,
            name=p.name,
            display_name=p.display_name or p.name,
            version=p.version,
            template=p.template,
            output_schema=p.output_schema or {},
            is_active=p.is_active,
        )
        for p in result.scalars().all()
    ]


@router.post("", response_model=PromptOut, status_code=201, dependencies=[Depends(admin_auth)])
async def create_prompt(body: PromptIn, session: AsyncSession = Depends(get_session)):
    from ..core.engine_models import PromptTemplate

    result = await session.execute(select(PromptTemplate).where(PromptTemplate.name == body.name))
    existing = result.scalars().all()
    next_version = max((p.version for p in existing), default=0) + 1

    p = PromptTemplate(
        name=body.name,
        display_name=body.display_name or body.name,
        version=next_version,
        template=body.template,
        output_schema=body.output_schema,
        is_active=body.is_active,
    )
    session.add(p)
    await session.commit()
    await session.refresh(p)
    return PromptOut(
        id=p.id,
        name=p.name,
        display_name=p.display_name or p.name,
        version=p.version,
        template=p.template,
        output_schema=p.output_schema or {},
        is_active=p.is_active,
    )


@router.post("/preview", dependencies=[Depends(admin_auth)])
async def preview_prompt(template: str = Body(..., embed=True)):
    rendered = preview_template(template)
    return {"rendered": rendered}


__all__ = ["router"]
