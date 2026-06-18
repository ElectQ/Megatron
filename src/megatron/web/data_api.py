from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.db import get_session
from ..ingest.service import IngestService

router = APIRouter(prefix="/api/items", tags=["items"])


class ItemOut(BaseModel):
    id: int
    item_id: str
    source: str
    source_ref: str
    author: str
    author_name: str
    content: str
    url: str
    published_at: datetime
    collect_date: str
    is_retweet: bool
    is_quote: bool
    tags: list
    links: list
    metrics: dict
    importance_score: float
    analysis_state: str


class ItemDetail(ItemOut):
    title: str
    language: str
    collected_at: datetime
    media: dict
    raw: dict


class ItemsPage(BaseModel):
    total: int
    total_returned: int
    page: int
    page_size: int
    items: list[ItemOut]


def _to_out(rec) -> ItemOut:
    return ItemOut(
        id=rec.id,
        item_id=rec.item_id,
        source=rec.source,
        source_ref=rec.source_ref,
        author=rec.author,
        author_name=rec.author_name,
        content=rec.content,
        url=rec.url,
        published_at=rec.published_at,
        collect_date=rec.collect_date or "",
        is_retweet=rec.is_retweet,
        is_quote=rec.is_quote,
        tags=rec.tags or [],
        links=rec.links or [],
        metrics=rec.metrics or {},
        importance_score=rec.importance_score,
        analysis_state=rec.analysis_state,
    )


@router.get("", response_model=ItemsPage)
async def list_items(
    source: str | None = None,
    collect_date: str | None = None,
    author: str | None = None,
    keyword: str | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    """List items with filtering + pagination.

    Query params:
        source: filter by source (twitter)
        collect_date: filter by collect_date (YYYY-MM-DD)
        author: filter by author handle (substring match)
        keyword: search content (case-insensitive substring)
        limit / offset: pagination
    """
    from ..core.models import ItemRecord

    conditions = []
    if source:
        conditions.append(ItemRecord.source == source)
    if collect_date:
        conditions.append(ItemRecord.collect_date == collect_date)
    if author:
        conditions.append(ItemRecord.author.ilike(f"%{author}%"))
    if keyword:
        conditions.append(ItemRecord.content.ilike(f"%{keyword}%"))

    # Total count (for pagination)
    count_stmt = select(func.count(ItemRecord.id))
    for cond in conditions:
        count_stmt = count_stmt.where(cond)
    total = (await session.execute(count_stmt)).scalar_one()

    # Query items
    stmt = select(ItemRecord).order_by(ItemRecord.published_at.desc())
    for cond in conditions:
        stmt = stmt.where(cond)
    stmt = stmt.limit(limit).offset(offset)
    records = (await session.execute(stmt)).scalars().all()

    page = (offset // limit) + 1 if limit else 1
    return ItemsPage(
        total=total,
        total_returned=len(records),
        page=page,
        page_size=limit,
        items=[_to_out(r) for r in records],
    )


@router.get("/{item_id}", response_model=ItemDetail)
async def get_item(item_id: int, session: AsyncSession = Depends(get_session)):
    service = IngestService(session)
    rec = await service.get_item(item_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Item not found")
    return ItemDetail(
        **_to_out(rec).model_dump(),
        title=rec.title,
        language=rec.language,
        collected_at=rec.collected_at,
        media=rec.media or {},
        raw=rec.raw or {},
    )


__all__ = ["router"]
