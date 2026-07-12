"""The source registry: YAML files in, `source_configs` rows out.

YAML is the truth. `sync_specs()` projects the files onto the table; rows it owns
carry `managed_by="yaml"` and are read-only in the UI. A spec that disappears from
disk is *disabled*, never deleted — its `items` must keep resolving, and the
dedup key depends on the source label still existing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from ..core.models import SourceConfig
from .spec import SourceSpec

logger = get_logger(__name__)


class SpecError(ValueError):
    """A YAML source file is malformed. Carries the path for a usable message."""

    def __init__(self, path: Path, message: str):
        self.path = path
        super().__init__(f"{path}: {message}")


# ---------------------------------------------------------------- YAML loading


def load_specs(sources_dir: str | Path) -> tuple[list[SourceSpec], list[SpecError]]:
    """Parse every *.yaml under `sources_dir`.

    Returns (specs, errors). A broken file never takes down the boot: it is
    reported and skipped, so one typo cannot silence every other source.
    """
    import yaml

    root = Path(sources_dir)
    if not root.is_dir():
        logger.info("sources.no_dir", path=str(root))
        return [], []

    specs: list[SourceSpec] = []
    errors: list[SpecError] = []
    seen: dict[str, Path] = {}

    for path in sorted([*root.glob("*.yaml"), *root.glob("*.yml")]):
        try:
            raw = yaml.safe_load(path.read_text()) or {}
        except Exception as e:
            errors.append(SpecError(path, f"invalid YAML: {e}"))
            continue
        if not isinstance(raw, dict):
            errors.append(SpecError(path, "expected a mapping at the top level"))
            continue
        try:
            spec = SourceSpec(**raw)
        except Exception as e:
            errors.append(SpecError(path, str(e)))
            continue
        if spec.source_id in seen:
            errors.append(
                SpecError(path, f"duplicate source_id '{spec.source_id}' (also in {seen[spec.source_id].name})")
            )
            continue
        seen[spec.source_id] = path
        specs.append(spec)

    logger.info("sources.loaded", path=str(root), specs=len(specs), errors=len(errors))
    return specs, errors


# ------------------------------------------------------------------ projection


def _source_type_for(adapter: str) -> str:
    """Keep the legacy discriminator consistent so the MCP admin API still works."""
    return "mcp" if adapter == "mcp_query" else "native"


async def sync_specs(session: AsyncSession, specs: list[SourceSpec]) -> dict[str, int]:
    """Upsert specs onto `source_configs`. Returns a {created, updated, disabled} count."""
    now = datetime.now(timezone.utc)
    created = updated = disabled = 0

    rows = {
        sc.name: sc
        for sc in (await session.execute(select(SourceConfig))).scalars().all()
    }
    spec_ids = set()

    for spec in specs:
        spec_ids.add(spec.source_id)
        sc = rows.get(spec.source_id)
        if sc is None:
            session.add(
                SourceConfig(
                    name=spec.source_id,
                    display_name=spec.display_name or spec.source_id,
                    kind=spec.kind,
                    source_type=_source_type_for(spec.adapter),
                    adapter=spec.adapter,
                    audience=spec.audience_scalar,
                    config=spec.db_config(),
                    schedule_expect=spec.schedule_expect.model_dump(),
                    managed_by="yaml",
                    enabled=spec.enabled,
                    last_sync_at=now,
                )
            )
            created += 1
            continue

        sc.display_name = spec.display_name or spec.source_id
        sc.kind = spec.kind
        sc.source_type = _source_type_for(spec.adapter)
        sc.adapter = spec.adapter
        sc.audience = spec.audience_scalar
        sc.config = spec.db_config()
        sc.schedule_expect = spec.schedule_expect.model_dump()
        sc.managed_by = "yaml"
        sc.enabled = spec.enabled
        sc.last_sync_at = now
        updated += 1

    # A YAML source that vanished from disk is disabled, not dropped: its items
    # stay addressable and the dedup key keeps working.
    for name, sc in rows.items():
        if sc.managed_by == "yaml" and name not in spec_ids and sc.enabled:
            sc.enabled = False
            disabled += 1
            logger.warning("sources.spec_removed_disabling", source=name)

    await session.commit()
    logger.info("sources.synced", created=created, updated=updated, disabled=disabled)
    return {"created": created, "updated": updated, "disabled": disabled}


async def sync_from_dir(session: AsyncSession, sources_dir: str | Path) -> dict[str, Any]:
    specs, errors = load_specs(sources_dir)
    for err in errors:
        logger.error("sources.spec_invalid", path=str(err.path), error=str(err))
    counts = await sync_specs(session, specs)
    return {**counts, "errors": [str(e) for e in errors]}


# --------------------------------------------------------------------- queries


async def get_source(session: AsyncSession, source_id: str) -> SourceConfig | None:
    return (
        await session.execute(select(SourceConfig).where(SourceConfig.name == source_id))
    ).scalar_one_or_none()


async def resolve_source_id(session: AsyncSession, given: str) -> SourceConfig | None:
    """Find a source by its id, falling back to `config.legacy_aliases`.

    The alias lookup exists so an old collector still POSTing to `/api/ingest/soundwave`
    lands on the renamed source instead of 404ing. Aliases are matched in Python,
    not SQL: JSON containment operators differ across SQLite and Postgres, and the
    registry is small enough that it does not matter.
    """
    sc = await get_source(session, given)
    if sc is not None:
        return sc

    for row in (await session.execute(select(SourceConfig))).scalars().all():
        aliases = (row.config or {}).get("legacy_aliases") or []
        if given in aliases:
            return row
    return None


async def list_sources(
    session: AsyncSession,
    adapter: str | None = None,
    enabled_only: bool = True,
) -> list[SourceConfig]:
    stmt = select(SourceConfig).order_by(SourceConfig.name)
    if adapter:
        stmt = stmt.where(SourceConfig.adapter == adapter)
    if enabled_only:
        stmt = stmt.where(SourceConfig.enabled.is_(True))
    return list((await session.execute(stmt)).scalars().all())


def to_api(sc: SourceConfig) -> dict:
    """Serialize a row into the spec's §3.1 shape (audience back to a list)."""
    audience = ["personal", "public"] if sc.audience == "both" else [sc.audience or "personal"]
    config = dict(sc.config or {})
    fetch = config.get("fetch") or {}
    if fetch.get("headers"):
        # Never hand back a resolved credential; the spec keeps ${VAR} anyway,
        # but a hand-written db row might not.
        fetch = {**fetch, "headers": {k: "***" for k in fetch["headers"]}}
        config = {**config, "fetch": fetch}
    return {
        "source_id": sc.name,
        "display_name": sc.display_name or sc.name,
        "kind": sc.kind,
        "adapter": sc.adapter,
        "audience": audience,
        "enabled": sc.enabled,
        "managed_by": sc.managed_by,
        "schedule_expect": sc.schedule_expect or {},
        "config": config,
        "last_sync_at": sc.last_sync_at.isoformat() if sc.last_sync_at else None,
    }


__all__ = [
    "SpecError",
    "get_source",
    "list_sources",
    "load_specs",
    "resolve_source_id",
    "sync_from_dir",
    "sync_specs",
    "to_api",
]
