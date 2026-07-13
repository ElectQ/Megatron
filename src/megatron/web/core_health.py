"""Helpers for the unauthenticated /health endpoint."""

from __future__ import annotations

from ..core.db import async_session_factory
from ..core.logging import get_logger
from ..ingest.registry import list_sources

logger = get_logger(__name__)


async def registered_sources() -> list[str]:
    """The enabled source_ids. Was hardcoded to ["twitter"], which stopped being
    true the moment the registry existed."""
    try:
        async with async_session_factory() as session:
            return [sc.name for sc in await list_sources(session, enabled_only=True)]
    except Exception as e:
        # /health must answer even when the DB is unhappy — that is the point of it.
        logger.warning("health.sources_unavailable", error=str(e))
        return []


__all__ = ["registered_sources"]
