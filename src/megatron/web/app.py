from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from ..config import settings
from ..core.db import dispose_db, init_db
from ..core.logging import get_logger, setup_logging
from ..core.security import RedirectLoginException
from ..ingest import api as ingest_api
from ..scheduler import shutdown_scheduler, start_scheduler
from . import (
    channels_api,
    data_api,
    modules_api,
    prompts_api,
    providers_api,
    runs_api,
    schedules_api,
    stats_api,
    ui,
)

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(level="INFO")
    from ..engine import agent_loop as _agent  # noqa: F401  trigger registration
    from ..plugins import filters as _filters  # noqa: F401  trigger registration
    from ..plugins import sources as _sources  # noqa: F401  trigger registration
    from ..plugins import tools as _tools  # noqa: F401  trigger registration
    from ..plugins import webhooks as _webhooks  # noqa: F401  trigger registration

    await init_db()
    start_scheduler()
    logger.info("app.started", env=settings.env)
    yield
    shutdown_scheduler()
    await dispose_db()
    logger.info("app.stopped")


app = FastAPI(
    title="Megatron",
    description="Prompt-driven LLM analysis hub",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key_for_sessions,
    session_cookie="megatron_session",
    max_age=86400,
    same_site="lax",
    https_only=False,
)

app.include_router(ingest_api.router)
app.include_router(data_api.router)
app.include_router(providers_api.router)
app.include_router(prompts_api.router)
app.include_router(modules_api.router)
app.include_router(runs_api.router)
app.include_router(channels_api.router)
app.include_router(schedules_api.router)
app.include_router(stats_api.router)
app.include_router(ui.router)


@app.exception_handler(RedirectLoginException)
async def redirect_to_login(request: Request, exc: RedirectLoginException):
    return RedirectResponse("/ui/login", status_code=303)


@app.get("/")
async def root():
    return RedirectResponse("/ui/dashboard")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "megatron",
        "sources": ["twitter"],
        "version": "0.2.0",
    }
