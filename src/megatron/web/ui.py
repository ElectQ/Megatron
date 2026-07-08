from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..config import get_admin_token
from ..core.security import admin_auth
from .i18n import SUPPORTED_LANGS, get_lang, make_translator, normalize_lang

router = APIRouter(prefix="/ui", tags=["ui"])
templates = Jinja2Templates(
    directory=str(__import__("pathlib").Path(__file__).parent / "templates")
)


def _internal_base_url(request: Request) -> str:
    """Trusted loopback base URL for internal self-calls.

    We must NOT derive this from request.base_url: that honors the client's
    Host / X-Forwarded-* headers, so a forged Host would make us send the admin
    Bearer token to an attacker-controlled host (SSRF + credential leak). The
    ASGI scope's ``server`` is the address the app is actually bound to and is
    not client-controllable; we pin to loopback with that port so the token can
    only ever travel to ourselves.
    """
    server = request.scope.get("server")
    port = server[1] if server and len(server) > 1 and server[1] else 80
    return f"http://127.0.0.1:{port}"


async def _api_get(request: Request, path: str) -> Any:
    """Call internal API with the admin token (fallback for API auth)."""
    base_url = _internal_base_url(request)
    token = get_admin_token()
    async with httpx.AsyncClient() as c:
        r = await c.get(
            f"{base_url}{path}",
            headers={"Authorization": f"Bearer {token}"},
        )
        return r.json() if r.status_code == 200 else []


async def _api_post(request: Request, path: str, json_body: dict) -> Any:
    base_url = _internal_base_url(request)
    token = get_admin_token()
    async with httpx.AsyncClient() as c:
        r = await c.post(
            f"{base_url}{path}",
            headers={"Authorization": f"Bearer {token}"},
            json=json_body,
        )
        return {"status": r.status_code, "body": r.json() if r.status_code < 500 else r.text}


def _render(request: Request, name: str, active: str, **ctx) -> HTMLResponse:
    user = request.session.get("user", {})
    lang = get_lang(request)
    return templates.TemplateResponse(
        request,
        name,
        {
            "request": request,
            "active": active,
            "current_user": user.get("display_name") or user.get("username", ""),
            "t": make_translator(lang),
            "lang": lang,
            "langs": SUPPORTED_LANGS,
            **ctx,
        },
    )


@router.get("/lang/{code}")
async def set_lang(request: Request, code: str):
    """Persist the UI language in a cookie and return to the current page."""
    from urllib.parse import urlparse

    ref_path = urlparse(request.headers.get("referer", "")).path
    target = ref_path if ref_path.startswith("/ui") else "/ui/dashboard"
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie(
        "lang", normalize_lang(code), max_age=31536000, samesite="lax", path="/"
    )
    return resp


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return _render(request, "login.html", "login")


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    from ..core.db import async_session_factory
    from ..core.security import authenticate_user

    async with async_session_factory() as session:
        user = await authenticate_user(session, username, password)
    if user:
        request.session["user"] = {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name or user.username,
        }
        return RedirectResponse("/ui/dashboard", status_code=303)
    return RedirectResponse("/ui/login?error=1", status_code=303)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/ui/login", status_code=303)


@router.get("/dashboard", response_class=HTMLResponse, dependencies=[Depends(admin_auth)])
async def dashboard(request: Request):
    items, providers, modules, channels, runs, sources = await asyncio.gather(
        _api_get(request, "/api/items?limit=1"),
        _api_get(request, "/api/admin/providers"),
        _api_get(request, "/api/admin/modules"),
        _api_get(request, "/api/admin/channels"),
        _api_get(request, "/api/admin/runs?limit=5"),
        _api_get(request, "/api/admin/source-configs"),
    )
    item_total = items.get("total", 0) if isinstance(items, dict) else len(items)
    return _render(
        request,
        "dashboard.html",
        "dashboard",
        item_count=item_total,
        source_count=len(sources) if isinstance(sources, list) else 0,
        providers=providers,
        modules=modules,
        channels=channels,
        runs=runs,
    )


@router.get("/data/collected", response_class=HTMLResponse, dependencies=[Depends(admin_auth)])
async def data_collected_page(
    request: Request,
    author: str = "",
    keyword: str = "",
    collect_date: str = "",
    page: int = 1,
    page_size: int = 50,
):
    import urllib.parse

    params = {"limit": page_size, "offset": (page - 1) * page_size}
    if author:
        params["author"] = author
    if keyword:
        params["keyword"] = keyword
    if collect_date:
        params["collect_date"] = collect_date
    qs = urllib.parse.urlencode(params)
    data = await _api_get(request, f"/api/items?{qs}")

    total = data.get("total", 0) if isinstance(data, dict) else 0
    total_pages = max(1, (total + page_size - 1) // page_size)

    def _page_qs(p):
        p_params = dict(params)
        p_params["limit"] = None
        p_params["offset"] = None
        p_params["page"] = p
        return urllib.parse.urlencode({k: v for k, v in p_params.items() if v is not None})

    return _render(
        request,
        "items.html",
        "data_collected",
        page=data
        if isinstance(data, dict)
        else {"items": [], "total": 0, "page": 1, "page_size": 50, "total_returned": 0},
        total_pages=total_pages,
        filters={"author": author, "keyword": keyword, "collect_date": collect_date},
        prev_page=_page_qs(page - 1) if page > 1 else "",
        next_page=_page_qs(page + 1) if page < total_pages else "",
    )


@router.get("/items", response_class=HTMLResponse, dependencies=[Depends(admin_auth)])
async def items_redirect():
    return RedirectResponse("/ui/data/collected", status_code=302)


@router.get("/tasks", response_class=HTMLResponse, dependencies=[Depends(admin_auth)])
async def tasks_page(request: Request, edit: int | None = None):
    modules, providers, prompts, channels, opts, stats_rows = await asyncio.gather(
        _api_get(request, "/api/admin/modules"),
        _api_get(request, "/api/admin/providers"),
        _api_get(request, "/api/admin/prompts"),
        _api_get(request, "/api/admin/channels"),
        _api_get(request, "/api/admin/modules/options"),
        _api_get(request, "/api/admin/stats/per-module"),
    )
    stats = (
        {row["module_id"]: row for row in stats_rows}
        if isinstance(stats_rows, list)
        else {}
    )
    edit_module = next((m for m in modules if m["id"] == edit), None) if edit else None
    return _render(
        request,
        "modules.html",
        "tasks",
        modules=modules,
        providers=providers,
        prompts=prompts,
        channels=channels,
        opts=opts,
        stats=stats,
        edit_module=edit_module,
    )


@router.get("/modules", response_class=HTMLResponse, dependencies=[Depends(admin_auth)])
async def modules_redirect():
    return RedirectResponse("/ui/tasks", status_code=302)


@router.get("/prompts", response_class=HTMLResponse, dependencies=[Depends(admin_auth)])
async def prompts_page(request: Request):
    prompts = await _api_get(request, "/api/admin/prompts")
    return _render(request, "prompts.html", "prompts", prompts=prompts)


@router.get("/providers", response_class=HTMLResponse, dependencies=[Depends(admin_auth)])
async def providers_page(request: Request):
    providers = await _api_get(request, "/api/admin/providers")
    return _render(request, "providers.html", "providers", providers=providers)


@router.get("/channels", response_class=HTMLResponse, dependencies=[Depends(admin_auth)])
async def channels_page(request: Request):
    channels, opts = await asyncio.gather(
        _api_get(request, "/api/admin/channels"),
        _api_get(request, "/api/admin/channels/options"),
    )
    return _render(request, "channels.html", "channels", channels=channels, opts=opts)


@router.get("/schedules", response_class=HTMLResponse, dependencies=[Depends(admin_auth)])
async def schedules_page(request: Request):
    schedules, modules = await asyncio.gather(
        _api_get(request, "/api/admin/schedules"),
        _api_get(request, "/api/admin/modules"),
    )
    return _render(request, "schedules.html", "schedules", schedules=schedules, modules=modules)


@router.get("/runs", response_class=HTMLResponse, dependencies=[Depends(admin_auth)])
async def runs_page(
    request: Request,
    module_id: int | None = None,
    date: str = "",
):
    import urllib.parse

    params = {"limit": 30}
    if module_id:
        params["module_id"] = module_id
    if date:
        params["date"] = date
    qs = urllib.parse.urlencode(params)
    runs = await _api_get(request, f"/api/admin/runs?{qs}")
    return _render(
        request,
        "runs.html",
        "tasks",
        runs=runs,
        filters={"date": date},
    )


@router.get("/data/sources", response_class=HTMLResponse, dependencies=[Depends(admin_auth)])
async def data_sources_page(request: Request):
    mcp_servers, source_configs = await asyncio.gather(
        _api_get(request, "/api/admin/mcp-servers"),
        _api_get(request, "/api/admin/source-configs"),
    )
    return _render(
        request,
        "sources.html",
        "data_sources",
        mcp_servers=mcp_servers,
        source_configs=source_configs,
    )


@router.get("/data/analyzed", response_class=HTMLResponse, dependencies=[Depends(admin_auth)])
async def data_analyzed_page(
    request: Request,
    module_id: int | None = None,
    date: str = "",
):
    import urllib.parse

    params = {"limit": 30}
    if module_id:
        params["module_id"] = module_id
    if date:
        params["date"] = date
    qs = urllib.parse.urlencode(params)
    runs = await _api_get(request, f"/api/admin/runs?{qs}")
    return _render(
        request,
        "analyzed.html",
        "data_analyzed",
        runs=runs,
        filters={"date": date, "module_id": module_id},
    )


__all__ = ["router"]
