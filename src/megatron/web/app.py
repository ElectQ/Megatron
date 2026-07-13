from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from ..config import settings
from ..core.db import dispose_db, init_db
from ..core.logging import get_logger, setup_logging
from .. import __version__
from ..core.security import RedirectLoginException, validate_runtime_settings
from ..ingest import api as ingest_api
from ..scheduler import shutdown_scheduler, start_scheduler
from . import (
    channels_api,
    data_api,
    digests_api,
    mcp_api,
    modules_api,
    policy_api,
    prompts_api,
    providers_api,
    public_api,
    publish_api,
    runs_api,
    day_api,
    schedules_api,
    settings_api,
    sources_api,
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
    from ..core.bootstrap import bootstrap

    await bootstrap(None)

    # After bootstrap: it mints and persists the session/admin/ingest secrets,
    # so validating before it would flag freshly-generatable secrets as weak.
    validate_runtime_settings()

    # Recover runs interrupted by the previous shutdown/crash so their modules
    # are not blocked forever by the active-run guard.
    from ..core.db import async_session_factory
    from ..engine.runner import reset_interrupted_runs

    async with async_session_factory() as session:
        recovered = await reset_interrupted_runs(session)
    if recovered:
        logger.info("app.reset_interrupted_runs", count=recovered)

    start_scheduler()
    logger.info("app.started", env=settings.env)
    yield
    shutdown_scheduler()
    await dispose_db()
    logger.info("app.stopped")


app = FastAPI(
    title="Megatron",
    description="Prompt-driven LLM analysis hub",
    version=__version__,
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

app.mount("/static", StaticFiles(directory="src/megatron/web/static"), name="static")

app.include_router(ingest_api.router)
app.include_router(data_api.router)
app.include_router(mcp_api.router)
app.include_router(providers_api.router)
app.include_router(prompts_api.router)
app.include_router(modules_api.router)
app.include_router(runs_api.router)
app.include_router(channels_api.router)
app.include_router(schedules_api.router)
app.include_router(stats_api.router)
app.include_router(sources_api.router)
app.include_router(digests_api.router)
app.include_router(policy_api.router)
app.include_router(settings_api.router)
app.include_router(publish_api.router)
app.include_router(day_api.router)
app.include_router(ui.router)


@app.exception_handler(RedirectLoginException)
async def redirect_to_login(request: Request, exc: RedirectLoginException):
    return RedirectResponse("/ui/login", status_code=303)


@app.get("/health")
async def health():
    from .core_health import registered_sources

    return {
        "status": "ok",
        "service": "megatron",
        "sources": await registered_sources(),
        "version": __version__,
    }


# The public frontend owns `/` and the `/{lang}` catch-all, so it is mounted LAST —
# after every specific route (/ui, /api, /day, /health, /r, /static) so its
# single-segment `/{lang}` cannot shadow them.
app.include_router(public_api.router)
