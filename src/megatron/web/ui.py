from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..config import settings
from ..core.security import admin_auth

router = APIRouter(prefix="/ui", tags=["ui"])
templates = Jinja2Templates(
    directory=str(__import__("pathlib").Path(__file__).parent / "templates")
)


async def _api_get(request: Request, path: str) -> Any:
    """Call internal API with the admin token (fallback for API auth)."""
    base_url = str(request.base_url).rstrip("/")
    token = settings.admin_token
    async with httpx.AsyncClient() as c:
        r = await c.get(
            f"{base_url}{path}",
            headers={"Authorization": f"Bearer {token}"},
        )
        return r.json() if r.status_code == 200 else []


async def _api_post(request: Request, path: str, json_body: dict) -> Any:
    base_url = str(request.base_url).rstrip("/")
    token = settings.admin_token
    async with httpx.AsyncClient() as c:
        r = await c.post(
            f"{base_url}{path}",
            headers={"Authorization": f"Bearer {token}"},
            json=json_body,
        )
        return {"status": r.status_code, "body": r.json() if r.status_code < 500 else r.text}


def _render(request: Request, name: str, active: str, **ctx) -> HTMLResponse:
    user = request.session.get("user", {})
    return templates.TemplateResponse(
        request,
        name,
        {
            "request": request,
            "active": active,
            "current_user": user.get("display_name") or user.get("username", ""),
            **ctx,
        },
    )


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
    items = await _api_get(request, "/api/items?limit=1")
    providers = await _api_get(request, "/api/admin/providers")
    modules = await _api_get(request, "/api/admin/modules")
    channels = await _api_get(request, "/api/admin/channels")
    runs = await _api_get(request, "/api/admin/runs?limit=5")
    return _render(
        request,
        "dashboard.html",
        "dashboard",
        item_count=len(items),
        providers=providers,
        modules=modules,
        channels=channels,
        runs=runs,
    )


@router.get("/items", response_class=HTMLResponse, dependencies=[Depends(admin_auth)])
async def items_page(
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

    # 分页链接参数
    def _page_qs(p):
        p_params = dict(params)
        p_params["limit"] = None
        p_params["offset"] = None
        p_params["page"] = p
        return urllib.parse.urlencode({k: v for k, v in p_params.items() if v is not None})

    return _render(
        request,
        "items.html",
        "items",
        page=data
        if isinstance(data, dict)
        else {"items": [], "total": 0, "page": 1, "page_size": 50, "total_returned": 0},
        total_pages=total_pages,
        filters={"author": author, "keyword": keyword, "collect_date": collect_date},
        prev_page=_page_qs(page - 1) if page > 1 else "",
        next_page=_page_qs(page + 1) if page < total_pages else "",
    )


@router.get("/modules", response_class=HTMLResponse, dependencies=[Depends(admin_auth)])
async def modules_page(request: Request, edit: int | None = None):
    modules = await _api_get(request, "/api/admin/modules")
    providers = await _api_get(request, "/api/admin/providers")
    prompts = await _api_get(request, "/api/admin/prompts")
    channels = await _api_get(request, "/api/admin/channels")
    opts = await _api_get(request, "/api/admin/modules/options")
    edit_module = next((m for m in modules if m["id"] == edit), None) if edit else None
    return _render(
        request,
        "modules.html",
        "modules",
        modules=modules,
        providers=providers,
        prompts=prompts,
        channels=channels,
        opts=opts,
        edit_module=edit_module,
    )


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
    channels = await _api_get(request, "/api/admin/channels")
    opts = await _api_get(request, "/api/admin/channels/options")
    return _render(request, "channels.html", "channels", channels=channels, opts=opts)


@router.get("/schedules", response_class=HTMLResponse, dependencies=[Depends(admin_auth)])
async def schedules_page(request: Request):
    schedules = await _api_get(request, "/api/admin/schedules")
    modules = await _api_get(request, "/api/admin/modules")
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
        "runs",
        runs=runs,
        filters={"date": date},
    )


__all__ = ["router"]
