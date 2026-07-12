"""Runtime system settings that live in the DB and are editable in the admin UI.

`base_url` is the one that matters today: every pushed link is built from it, and
it should be settable in the UI (set your domain once, no redeploy) rather than
baked into an env var. The env `MEGATRON_BASE_URL` is only the first-boot seed.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger

logger = get_logger(__name__)


async def seed_system_settings(session: AsyncSession) -> None:
    """Create the singleton row from the env seed if it does not exist yet."""
    from ..config import settings
    from .engine_models import SystemSetting

    row = (await session.execute(select(SystemSetting))).scalars().first()
    if row is not None:
        return
    session.add(SystemSetting(base_url=settings.base_url or ""))
    await session.commit()
    logger.info("sysconfig.seeded", base_url=settings.base_url)


async def resolve_base_url(session: AsyncSession) -> str:
    """The operative base_url: the DB row if set, else the env default."""
    from ..config import settings
    from .engine_models import SystemSetting

    row = (await session.execute(select(SystemSetting))).scalars().first()
    if row is not None and (row.base_url or "").strip():
        return row.base_url.strip()
    return settings.base_url


def is_local_base_url(url: str) -> bool:
    from urllib.parse import urlparse

    host = urlparse(url or "").hostname or ""
    return host in {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""} or host.endswith(".local")
