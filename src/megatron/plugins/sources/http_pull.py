"""Raw-HTTP source: poll a URL and map its payload into Items.

The point of this adapter is that adding a source costs a YAML file and no
Python. Everything it needs — endpoint, auth headers, and how to read the
response — comes from the source spec.

Polling happens out of band (the scheduler), so `live_fetch` stays False: an
analysis run always reads `items` from the database, never the network.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from ...core.logging import get_logger
from ...core.types import Item
from ...ingest.spec import FetchSpec, MapSpec
from .base import BaseSource, register_source
from .mapping import map_payload

logger = get_logger(__name__)

# RSS/Atom is already a normalized shape, so a spec need not describe it.
DEFAULT_RSS_MAP = MapSpec(
    items="$.entries",
    external_id="$.id",
    title="$.title",
    content="$.summary",
    url="$.link",
    author="$.author",
    published_at="$.published",
)


@register_source("http_pull")
class HttpPullSource(BaseSource):
    """Fetch a JSON or RSS endpoint described by a source spec.

    Config:
        source_label: the source_id items are stamped with
        fetch:  FetchSpec as a dict (from SourceConfig.config)
        map:    MapSpec as a dict (optional for RSS)
    """

    name = "http_pull"
    live_fetch = False

    def __init__(self, **config: Any):
        super().__init__(**config)
        self.source_label = config.get("source_label", "")
        fetch = config.get("fetch")
        if not fetch:
            raise ValueError("http_pull source requires a `fetch` config block")
        self.fetch_spec = FetchSpec(**fetch) if isinstance(fetch, dict) else fetch
        raw_map = config.get("map")
        if raw_map:
            self.map_spec = MapSpec(**raw_map) if isinstance(raw_map, dict) else raw_map
        elif self.fetch_spec.format == "rss":
            self.map_spec = DEFAULT_RSS_MAP
        else:
            raise ValueError("http_pull source with format=json requires a `map` config block")

    async def fetch(self, since: datetime | None = None) -> list[Item]:
        payload = await self._get_payload()
        collect_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        items = map_payload(
            payload,
            self.map_spec,
            source_id=self.source_label or self.name,
            collect_date=collect_date,
        )
        if since:
            items = [it for it in items if it.published_at >= since]
        logger.info(
            "source.http_pull.fetched",
            source=self.source_label,
            url=self.fetch_spec.url,  # unresolved: never log the expanded secrets
            format=self.fetch_spec.format,
            count=len(items),
        )
        return items

    async def _get_payload(self) -> Any:
        spec = self.fetch_spec
        async with httpx.AsyncClient(timeout=spec.timeout, follow_redirects=True) as client:
            resp = await client.request(
                spec.method,
                spec.resolved_url(),
                headers=spec.resolved_headers(),
                params=spec.resolved_params() or None,
                json=spec.body if spec.method == "POST" else None,
            )
            resp.raise_for_status()
            if spec.format == "rss":
                return _parse_feed(resp.text)
            return resp.json()


def _parse_feed(text: str) -> dict:
    """Normalize RSS 2.0 / Atom into {"entries": [...]} so one MapSpec fits both."""
    import feedparser

    parsed = feedparser.parse(text)
    entries = []
    for e in parsed.entries:
        entries.append(
            {
                "id": e.get("id") or e.get("link") or e.get("title", ""),
                "title": e.get("title", ""),
                "summary": e.get("summary", ""),
                "link": e.get("link", ""),
                "author": e.get("author", ""),
                "published": e.get("published") or e.get("updated") or "",
                "tags": [t.get("term", "") for t in (e.get("tags") or [])],
            }
        )
    return {"entries": entries, "feed": dict(parsed.feed) if parsed.feed else {}}


__all__ = ["HttpPullSource", "DEFAULT_RSS_MAP"]
