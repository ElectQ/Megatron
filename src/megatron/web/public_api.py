"""The public frontend — a world-readable, indexable blog of public digests.

Only content the analysis marked `public: true` appears here, stripped of personal
framing (see public_view). Language is a path prefix (/zh, /en) for a blog/SEO
feel, separate from the admin's cookie-based locale. The personal capability
pages (/day/...?k=) and the admin (/ui/...) are untouched.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.db import get_session
from .day_api import _latest_bundle
from .i18n import SUPPORTED_LANGS, make_translator
from .public_view import has_public, load_policy, public_days, public_view

router = APIRouter(tags=["public"])
templates = Jinja2Templates(directory="src/megatron/web/templates")

TIER_ORDER = ["must_see_push", "must_see_page", "recommend", "skim"]
# Public, neutral labels (translated via t()); not the admin's personal ones.
TIER_LABEL = {
    "must_see_push": "Top",
    "must_see_page": "Must-see",
    "recommend": "Recommended",
    "skim": "More",
}
# The hue each tier carries. Lives here, not in a template, because both the post
# page (section headers, filter tabs) and the home page (the per-tier breakdown on
# a card) colour by tier and must agree.
TIER_COLOR = {
    "must_see_push": "var(--t-top)",
    "must_see_page": "var(--t-must)",
    "recommend": "var(--t-rec)",
    "skim": "var(--t-more)",
}


def _render(request: Request, lang: str, name: str, here_suffix: str = "", **ctx) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        name,
        {
            "request": request,
            "lang": lang,
            "other_lang": "en" if lang == "zh" else "zh",
            "here_suffix": here_suffix,
            "t": make_translator(lang),
            "langs": SUPPORTED_LANGS,
            "tier_order": TIER_ORDER,
            "tier_label": TIER_LABEL,
            "tier_color": TIER_COLOR,
            **ctx,
        },
    )


def _lang_or_404(lang: str) -> str:
    if lang not in SUPPORTED_LANGS:
        raise HTTPException(status_code=404, detail="Not found")
    return lang


@router.get("/")
async def public_root(request: Request):
    """Direct access lands on the public frontend, Chinese-first unless the reader
    has chosen otherwise (cookie) or their browser clearly prefers English."""
    cookie = request.cookies.get("lang")
    if cookie in SUPPORTED_LANGS:
        lang = cookie
    else:
        accept = (request.headers.get("accept-language") or "").lower()
        lang = "en" if accept[:2] == "en" else "zh"
    return RedirectResponse(f"/{lang}", status_code=302)


@router.get("/{lang}", response_class=HTMLResponse)
async def public_home(lang: str, request: Request, session: AsyncSession = Depends(get_session)):
    _lang_or_404(lang)
    days = await public_days(session)
    return _render(request, lang, "public/home.html", days=days)


@router.get("/{lang}/{source_id}/{date}", response_class=HTMLResponse)
async def public_post(
    lang: str,
    source_id: str,
    date: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    _lang_or_404(lang)
    bundle = await _latest_bundle(session, date, source_id)
    policy = await load_policy(session)
    # Default private: no bundle, nothing effectively public, or the operator took
    # the day down → 404 (not 403; don't confirm a private day exists).
    if not bundle or not has_public(bundle, policy):
        raise HTTPException(status_code=404, detail="Not found")
    view = public_view(bundle, policy)
    return _render(request, lang, "public/post.html", here_suffix=f"/{source_id}/{date}", view=view)


__all__ = ["router"]
