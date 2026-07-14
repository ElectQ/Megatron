"""The unified ingest envelope (§3.2).

One shape for every collector, whatever it collects:

    {
      "schema_version": 1,
      "source_id": "twitter_security_list",
      "collect_date": "2026-07-12",
      "producer": {"name": "soundwave", "version": "0.3.1", "run_id": "..."},
      "items": [ {"external_id": "...", "content": "...", ...} ]
    }

Collectors describe facts. They do not send `tier` or `why_for_me` — those are
the analysis layer's output, and accepting them here would let a collector
decide what interrupts you.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..core.types import Item

SUPPORTED_SCHEMA_VERSIONS = frozenset({1})
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class IngestFlags(BaseModel):
    model_config = ConfigDict(extra="ignore")

    is_retweet: bool = False
    is_quote: bool = False


class IngestProducer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = ""
    version: str = ""
    run_id: str = ""


class IngestItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    external_id: str = Field(min_length=1, max_length=64)
    title: str = ""
    content: str = ""
    url: str = ""
    author: str = ""
    author_name: str = ""
    language: str = ""
    published_at: datetime | None = None
    collected_at: datetime | None = None
    tags: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    media: dict = Field(default_factory=dict)
    metrics: dict = Field(default_factory=dict)
    flags: IngestFlags = Field(default_factory=IngestFlags)
    # Source-specific enrichment. The GitHub follow feed attaches a `persona`
    # blob for the newly-followed target here; folded into `raw` on the way in so
    # it survives to the day page without a dedicated column.
    persona: dict = Field(default_factory=dict)
    raw: dict = Field(default_factory=dict)


class IngestEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: int
    source_id: str = ""
    source_ref: str = ""  # optional sub-stream (e.g. a Twitter list id)
    collect_date: str = ""
    collected_at: datetime | None = None
    producer: IngestProducer = Field(default_factory=IngestProducer)
    items: list[IngestItem] = Field(default_factory=list)

    @field_validator("collect_date")
    @classmethod
    def _date_shape(cls, v: str) -> str:
        if v and not _DATE_RE.match(v):
            raise ValueError("collect_date must be YYYY-MM-DD")
        return v


def looks_like_envelope(body: dict) -> bool:
    """Distinguish the unified envelope from a legacy plugin-specific payload."""
    return "schema_version" in body or "items" in body


def utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def envelope_to_items(
    env: IngestEnvelope,
    *,
    source_id: str,
    collect_date: str,
) -> list[Item]:
    """Map the envelope onto the internal Item.

    `source_id` becomes `Item.source` and `external_id` becomes `Item.id` — the
    two halves of the dedup key, so a replayed bundle is a no-op.
    """
    now = datetime.now(timezone.utc)
    batch_collected = env.collected_at or now

    items: list[Item] = []
    for raw in env.items:
        items.append(
            Item(
                id=raw.external_id,
                source=source_id,
                source_ref=env.source_ref,
                title=raw.title,
                content=raw.content,
                url=raw.url,
                author=raw.author,
                author_name=raw.author_name,
                language=raw.language,
                published_at=raw.published_at or raw.collected_at or batch_collected,
                collected_at=raw.collected_at or batch_collected,
                collect_date=collect_date,
                is_retweet=raw.flags.is_retweet,
                is_quote=raw.flags.is_quote,
                tags=list(raw.tags),
                links=list(raw.links),
                media=dict(raw.media),
                metrics=dict(raw.metrics),
                raw={**dict(raw.raw), **({"persona": dict(raw.persona)} if raw.persona else {})},
            )
        )
    return items


__all__ = [
    "SUPPORTED_SCHEMA_VERSIONS",
    "IngestEnvelope",
    "IngestFlags",
    "IngestItem",
    "IngestProducer",
    "envelope_to_items",
    "looks_like_envelope",
    "utc_today",
]
