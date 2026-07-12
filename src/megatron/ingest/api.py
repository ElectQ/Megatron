from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_ingest_settings
from ..core.db import get_session
from ..core.logging import get_logger
from ..core.models import SourceConfig
from ..core.security import IngestAuth
from ..core.types import Item
from ..plugins.sources.base import source_registry
from .errors import ingest_error
from .registry import resolve_source_id
from .schemas import (
    SUPPORTED_SCHEMA_VERSIONS,
    IngestEnvelope,
    envelope_to_items,
    looks_like_envelope,
    utc_today,
)
from .service import IngestService

logger = get_logger(__name__)

router = APIRouter(prefix="/api/ingest", tags=["ingest"])
# No pinned token: resolved per request, since bootstrap mints it after import.
_auth = IngestAuth()


class IngestResponse(BaseModel):
    source_id: str
    source: str  # == source_id; kept so existing collectors keep parsing the reply
    source_ref: str
    date: str
    ingested: int
    duplicated: int
    schema_version: int | None = None


@router.post("", response_model=IngestResponse, dependencies=[Depends(_auth)])
async def ingest_generic(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> IngestResponse:
    """Unified entry point. `source_id` must be in the body."""
    body = await _json_body(request)
    return await _ingest_envelope(session, body, path_source=None)


@router.post("/{source}", response_model=IngestResponse, dependencies=[Depends(_auth)])
async def ingest_payload(
    source: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> IngestResponse:
    """Receive a batch from a collector.

    Two shapes are accepted, chosen by the body:

    * the unified envelope (`schema_version` / `items`) — the way in;
    * a legacy plugin-specific payload, where `{source}` names a source plugin
      that parses the body itself. Deprecated, kept so a collector that has not
      migrated yet does not go dark.

    Idempotent either way: `(source_id, external_id)` is the dedup key.
    """
    body = await _json_body(request)
    if looks_like_envelope(body):
        return await _ingest_envelope(session, body, path_source=source)
    return await _ingest_legacy_plugin(session, body, source)


def _errors(e: ValidationError) -> list[dict]:
    """Flatten pydantic errors into something JSON-serialisable.

    `ValidationError.errors()` embeds the original exception under `ctx`, which
    the response encoder cannot serialise — the 400 would become a 500.
    """
    return [
        {
            "field": ".".join(str(p) for p in err.get("loc", ())),
            "message": err.get("msg", ""),
            "type": err.get("type", ""),
        }
        for err in e.errors()
    ]


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception as e:
        raise ingest_error(400, "invalid_json", f"Body is not valid JSON: {e}")
    if not isinstance(body, dict):
        raise ingest_error(400, "invalid_json", "Body must be a JSON object")
    return body


# ------------------------------------------------------------------- envelope


async def _ingest_envelope(
    session: AsyncSession,
    body: dict,
    path_source: str | None,
) -> IngestResponse:
    version = body.get("schema_version")
    if version is None:
        raise ingest_error(
            400,
            "schema_version_missing",
            f"Envelope requires `schema_version`. Supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)}",
        )
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ingest_error(
            400,
            "unsupported_schema_version",
            f"schema_version {version!r} is not supported. "
            f"Supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)}",
        )

    try:
        env = IngestEnvelope(**body)
    except ValidationError as e:
        raise ingest_error(400, "invalid_payload", "Envelope failed validation", _errors(e))

    body_source = env.source_id.strip()
    if path_source and body_source and path_source != body_source:
        raise ingest_error(
            400,
            "source_id_mismatch",
            f"URL says source '{path_source}' but the body says '{body_source}'",
        )
    given = body_source or path_source or ""
    if not given:
        raise ingest_error(
            400,
            "source_id_missing",
            "POST /api/ingest requires `source_id` in the body "
            "(or use POST /api/ingest/{source_id})",
        )

    sc = await _registered_source(session, given)

    # A derivable field must not turn a collector's run red.
    collect_date = env.collect_date or utc_today()
    defaulted = not env.collect_date

    items: list[Item] = envelope_to_items(env, source_id=sc.name, collect_date=collect_date)

    service = IngestService(session)
    ingested, duplicated = await service.ingest_items(items, mode="push", date=collect_date)

    logger.info(
        "ingest.envelope",
        source_id=sc.name,
        requested_as=given,
        collect_date=collect_date,
        collect_date_defaulted=defaulted,
        producer=env.producer.name,
        producer_run=env.producer.run_id,
        ingested=ingested,
        duplicated=duplicated,
    )

    return IngestResponse(
        source_id=sc.name,
        source=sc.name,
        source_ref=env.source_ref,
        date=collect_date,
        ingested=ingested,
        duplicated=duplicated,
        schema_version=env.schema_version,
    )


async def _registered_source(session: AsyncSession, given: str) -> SourceConfig:
    """Enforce register-before-push.

    Silently accepting an unknown source_id is how a typo becomes a second,
    invisible source that no analysis task ever reads.
    """
    sc = await resolve_source_id(session, given)
    if sc is None:
        if get_ingest_settings().ingest_auto_register:
            # Leave a disabled row an operator can enable from the UI — but still
            # reject the push. Auto-enabling would defeat the point of the gate.
            session.add(
                SourceConfig(
                    name=given[:64],
                    display_name=given[:128],
                    source_type="native",
                    adapter="http_push",
                    audience="personal",
                    managed_by="db",
                    enabled=False,
                    config={"auto_registered": True},
                    last_sync_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            logger.warning("ingest.auto_registered_disabled", source_id=given)
        raise ingest_error(
            404,
            "unknown_source",
            f"Source '{given}' is not registered. Declare it in a sources/*.yaml "
            "and run `megatron sources sync` (or POST /api/admin/sources/reload).",
        )
    if not sc.enabled:
        raise ingest_error(403, "source_disabled", f"Source '{sc.name}' is disabled")
    return sc


# --------------------------------------------------------------------- legacy


async def _ingest_legacy_plugin(
    session: AsyncSession,
    payload: dict,
    source: str,
) -> IngestResponse:
    """The pre-envelope path: `{source}` is a plugin that parses its own body."""
    if source not in source_registry:
        raise ingest_error(
            404,
            "unknown_source",
            f"Unknown source '{source}'. Send the unified envelope "
            f"(schema_version=1) or use one of: {source_registry.names()}",
        )

    logger.warning(
        "ingest.legacy_plugin_path",
        source=source,
        hint="send schema_version=1 envelopes instead; this path goes away in Phase 1",
    )

    plugin = source_registry.create(source, data=payload)
    items: list[Item] = await plugin.fetch()

    date = str(payload.get("date", ""))
    service = IngestService(session)
    ingested, duplicated = await service.ingest_items(items, mode="push", date=date)

    source_ref = items[0].source_ref if items else ""
    stored_source = items[0].source if items else source

    logger.info(
        "ingest.api",
        source=stored_source,
        source_ref=source_ref,
        ingested=ingested,
        duplicated=duplicated,
    )

    return IngestResponse(
        source_id=stored_source,
        source=stored_source,
        source_ref=source_ref,
        date=date,
        ingested=ingested,
        duplicated=duplicated,
    )


__all__ = ["router"]
