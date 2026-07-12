"""Read-only view of the source registry, plus a YAML re-sync trigger.

Sources declared in YAML are owned by their file. The API exposes them and can
re-read the directory, but it deliberately offers no create/update/delete: two
writers over one registry is how a source_id silently drifts from the collector
that is pushing to it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..core.db import get_session
from ..core.logging import get_logger
from ..core.security import admin_auth
from ..ingest.registry import get_source, list_sources, sync_from_dir, to_api

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/sources", tags=["sources"], dependencies=[Depends(admin_auth)])


@router.get("")
async def list_all(
    adapter: str = "",
    enabled_only: bool = False,
    session: AsyncSession = Depends(get_session),
):
    rows = await list_sources(session, adapter=adapter or None, enabled_only=enabled_only)
    return {"sources": [to_api(sc) for sc in rows], "sources_dir": settings.sources_dir}


@router.post("/reload")
async def reload_specs(session: AsyncSession = Depends(get_session)):
    """Re-read sources/*.yaml and project it onto the registry."""
    result = await sync_from_dir(session, settings.sources_dir)
    logger.info("sources.reloaded", **{k: v for k, v in result.items() if k != "errors"})
    return result


@router.get("/{source_id}")
async def get_one(source_id: str, session: AsyncSession = Depends(get_session)):
    sc = await get_source(session, source_id)
    if sc is None:
        raise HTTPException(status_code=404, detail=f"Unknown source '{source_id}'")
    return to_api(sc)


@router.get("/{source_id}/curl")
async def push_example(source_id: str, session: AsyncSession = Depends(get_session)):
    """A ready-to-paste push example. HTTP push is the primary way in."""
    sc = await get_source(session, source_id)
    if sc is None:
        raise HTTPException(status_code=404, detail=f"Unknown source '{source_id}'")
    if sc.adapter != "http_push":
        raise HTTPException(
            status_code=400,
            detail=f"Source '{source_id}' uses adapter '{sc.adapter}', which Megatron reads itself; "
            "nothing pushes to it.",
        )

    url = f"{settings.base_url.rstrip('/')}/api/ingest/{sc.name}"
    body = """{
  "schema_version": 1,
  "collect_date": "2026-07-12",
  "producer": {"name": "my-collector", "version": "0.1.0"},
  "items": [
    {
      "external_id": "1748402774835134821",
      "content": "…",
      "url": "https://example.com/item/1",
      "author": "handle",
      "published_at": "2026-07-12T08:00:00Z"
    }
  ]
}"""
    curl = (
        f'curl -fsS -X POST "{url}" \\\n'
        f'  -H "Authorization: Bearer $MEGATRON_INGEST_TOKEN" \\\n'
        f'  -H "Content-Type: application/json" \\\n'
        f"  -d '{body}'"
    )
    return {"source_id": sc.name, "ingest_url": url, "curl": curl, "example_body": body}


__all__ = ["router"]
