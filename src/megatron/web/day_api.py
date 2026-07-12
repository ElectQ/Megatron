"""The daily digest page — the thing the doorbell links to.

Not behind the admin login: it has to open from a phone, straight out of a chat
message. But it carries personal analysis ("why this matters to *you*"), so it
is not world-readable either. The compromise is a capability URL: the page is
served only with the right `?k=` and the token is unguessable and rotatable.

The page renders whatever the latest day-bundle run produced for that date, so it
reflects the most recent analysis without anything having to be regenerated.
"""

from __future__ import annotations

import hmac
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_day_token
from ..core.db import get_session
from ..core.engine_models import AnalysisRun
from ..core.logging import get_logger
from ..engine.bundle import BUNDLE_SCHEMA, TIERS

logger = get_logger(__name__)

router = APIRouter(tags=["day"])
templates = Jinja2Templates(directory="src/megatron/web/templates")

TIER_LABEL = {
    "must_see_push": "必看 · 已推送",
    "must_see_page": "必看",
    "recommend": "推荐",
    "skim": "速览",
}


def _authorized(key: str) -> bool:
    expected = get_day_token()
    return bool(key) and hmac.compare_digest(key, expected)


async def _latest_bundle(session: AsyncSession, date: str) -> dict | None:
    """The most recent completed day-bundle run for `date`.

    Scans recent runs rather than filtering in SQL: `result` is a JSON column and
    the containment operators differ between SQLite and Postgres. A handful of
    rows is cheap, and it keeps the query portable.
    """
    rows = (
        await session.execute(
            select(AnalysisRun)
            .where(AnalysisRun.status == "completed")
            .order_by(desc(AnalysisRun.id))
            .limit(200)
        )
    ).scalars().all()

    for run in rows:
        result = run.result or {}
        if result.get("schema") == BUNDLE_SCHEMA and result.get("date") == date:
            return result
    return None


@router.get("/day/{date}", response_class=HTMLResponse)
async def day_page(
    date: str,
    request: Request,
    k: str = "",
    session: AsyncSession = Depends(get_session),
):
    if not _authorized(k):
        # 404, not 403: a wrong key should not confirm that the date exists.
        raise HTTPException(status_code=404, detail="Not found")

    try:
        day = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found")

    bundle = await _latest_bundle(session, date)

    grouped = {tier: [] for tier in TIERS if tier != "drop"}
    if bundle:
        for item in bundle.get("items") or []:
            grouped.setdefault(item.get("tier", "skim"), []).append(item)

    token_qs = f"?k={k}"
    return templates.TemplateResponse(
        request,
        "day.html",
        {
            "request": request,
            "date": date,
            "prev_date": (day - timedelta(days=1)).strftime("%Y-%m-%d"),
            "next_date": (day + timedelta(days=1)).strftime("%Y-%m-%d"),
            "today": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "bundle": bundle,
            "grouped": grouped,
            "tier_label": TIER_LABEL,
            "tier_order": [t for t in TIERS if t != "drop"],
            "token_qs": token_qs,
        },
    )


__all__ = ["router"]
