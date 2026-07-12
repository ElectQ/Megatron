from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.db import get_session
from ..core.logging import get_logger
from ..core.security import IngestAuth
from ..core.types import Item
from ..plugins.sources.base import source_registry
from .service import IngestService

logger = get_logger(__name__)

router = APIRouter(prefix="/api/ingest", tags=["ingest"])
# No pinned token: resolved per request, since bootstrap mints it after import.
_auth = IngestAuth()


class IngestResponse(BaseModel):
    source: str
    source_ref: str
    date: str
    ingested: int
    duplicated: int


@router.post("/{source}", response_model=IngestResponse, dependencies=[Depends(_auth)])
async def ingest_payload(
    source: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Receive a raw payload from a data source (e.g. Soundwave push).

    The source plugin is responsible for parsing the payload body into Items.
    Idempotent: duplicate (source, item_id) pairs are skipped.
    """
    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    if source not in source_registry:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown source '{source}'; available: {source_registry.names()}",
        )

    plugin = source_registry.create(source, data=payload)
    items: list[Item] = await plugin.fetch()

    service = IngestService(session)
    ingested, duplicated = await service.ingest_items(
        items, mode="push", date=str(payload.get("date", ""))
    )

    logger.info(
        "ingest.api",
        source=source,
        source_ref=items[0].source_ref if items else "",
        ingested=ingested,
        duplicated=duplicated,
    )

    return IngestResponse(
        source=source,
        source_ref=items[0].source_ref if items else "",
        date=str(payload.get("date", "")),
        ingested=ingested,
        duplicated=duplicated,
    )


__all__ = ["router"]
