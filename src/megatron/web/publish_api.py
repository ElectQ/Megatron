"""Admin control over what the public blog shows.

The analysis decides `public` per item; this is where the operator overrules it —
pulling a whole day down, or dropping a single mis-marked item — without touching
the run, which stays the record of what the model actually produced.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.db import get_session
from ..core.engine_models import PublicationOverride
from ..core.security import admin_auth
from .public_view import Policy, is_public, latest_bundles, load_policy

router = APIRouter(prefix="/api/admin/publications", tags=["publications"])


class PublishIn(BaseModel):
    published: bool


def _day_state(bundle: dict, policy: Policy) -> dict:
    """One (source, date) as the admin page shows it: every item with both the
    model's call and the effective one, so a disagreement is visible."""
    source_id = bundle.get("source_id", "")
    date = bundle.get("date", "")
    items = []
    for it in bundle.get("items") or []:
        item_id = str(it.get("id", ""))
        # Tri-state: True/False are the model's explicit calls, None means it did
        # not say — which, inside a publishable source, *means publish*. Rendering
        # None as "private" would invert what it actually does.
        by_model = it.get("public") if isinstance(it.get("public"), bool) else None
        effective = is_public(it, source_id, date, policy)
        items.append(
            {
                "id": item_id,
                "tier": it.get("tier", "skim"),
                "one_liner": it.get("one_liner") or (it.get("content") or "")[:120],
                "url": it.get("url") or it.get("original_url") or "",
                "topics": it.get("topics") or [],
                "public_by_model": by_model,
                "public": effective,
                # Only an explicit call can be overruled; "didn't say" is not a call.
                "overridden": by_model is not None and effective != by_model,
            }
        )
    day_override = policy.overrides.days.get((source_id, date))
    source_public = policy.source_public(source_id)
    live = [i for i in items if i["public"]]
    return {
        "source_id": source_id,
        "date": date,
        "title": bundle.get("title") or source_id,
        "total": len(items),
        "public_count": len(live),
        # False = operator took the day down. None = never touched.
        "day_published": day_override,
        # The source's hard gate. A personal source can never be published from
        # here — say so, rather than offering a toggle that does nothing.
        "source_public": source_public,
        # What the reader actually gets.
        "live": source_public and day_override is not False and bool(live),
        "items": items,
    }


@router.get("", dependencies=[Depends(admin_auth)])
async def list_publications(session: AsyncSession = Depends(get_session)):
    """Every analysed day, newest first — what is on the blog and what is held back."""
    policy = await load_policy(session)
    return [_day_state(b, policy) for b in await latest_bundles(session)]


async def _set(
    session: AsyncSession, source_id: str, date: str, item_id: str, published: bool
) -> None:
    row = (
        (
            await session.execute(
                select(PublicationOverride).where(
                    PublicationOverride.source_id == source_id,
                    PublicationOverride.date == date,
                    PublicationOverride.item_id == item_id,
                )
            )
        )
        .scalars()
        .first()
    )
    if row:
        row.published = published
        row.updated_at = datetime.now(timezone.utc)
    else:
        session.add(
            PublicationOverride(
                source_id=source_id,
                date=date,
                item_id=item_id,
                published=published,
                updated_at=datetime.now(timezone.utc),
            )
        )
    await session.commit()


@router.put("/{source_id}/{date}", dependencies=[Depends(admin_auth)])
async def set_day(
    source_id: str,
    date: str,
    body: PublishIn,
    session: AsyncSession = Depends(get_session),
):
    """Publish or take down a whole day. Taking it down 404s the page outright,
    whatever its items say."""
    await _set(session, source_id, date, "", body.published)
    policy = await load_policy(session)
    for b in await latest_bundles(session):
        if b.get("source_id") == source_id and b.get("date") == date:
            return _day_state(b, policy)
    raise HTTPException(404, "No analysed bundle for that source/date")


@router.put("/{source_id}/{date}/items/{item_id}", dependencies=[Depends(admin_auth)])
async def set_item(
    source_id: str,
    date: str,
    item_id: str,
    body: PublishIn,
    session: AsyncSession = Depends(get_session),
):
    """Publish or drop a single item — the fix for one bad call by the model,
    without losing the rest of the day."""
    await _set(session, source_id, date, item_id, body.published)
    policy = await load_policy(session)
    for b in await latest_bundles(session):
        if b.get("source_id") == source_id and b.get("date") == date:
            return _day_state(b, policy)
    raise HTTPException(404, "No analysed bundle for that source/date")


@router.delete("/{source_id}/{date}", dependencies=[Depends(admin_auth)])
async def clear_overrides(
    source_id: str,
    date: str,
    session: AsyncSession = Depends(get_session),
):
    """Drop every override for a day — hand the decision back to the analysis."""
    rows = (
        (
            await session.execute(
                select(PublicationOverride).where(
                    PublicationOverride.source_id == source_id,
                    PublicationOverride.date == date,
                )
            )
        )
        .scalars()
        .all()
    )
    for r in rows:
        await session.delete(r)
    await session.commit()
    policy = await load_policy(session)
    for b in await latest_bundles(session):
        if b.get("source_id") == source_id and b.get("date") == date:
            return _day_state(b, policy)
    raise HTTPException(404, "No analysed bundle for that source/date")


__all__ = ["router"]
