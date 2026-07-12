"""Pull day bundles that a collector already publishes in our envelope format.

Soundwave publishes `bundles/<date>.json` — and that file *is* a
`schema_version: 1` envelope: same `source_id`, same `collect_date`, same
`items[]` with `external_id` / `metrics` / `flags`. So there is nothing to map.
This adapter fetches it and hands it to the very same parser the push endpoint
uses, which means a bundle behaves identically whether the collector pushes it
to us or we go and get it.

    bundles/index.json  ->  {"latest": "...", "days": [{"date", "count", "sha256"}]}
    bundles/<date>.json ->  a §3.2 envelope

Days already at or below the watermark are skipped, so a poll is cheap. The
`sha256` from the index is verified against the bytes we downloaded: the index
and the day files are separate objects on the CDN and can be served out of step,
so a mismatch means we caught a torn read and should retry next tick rather than
ingest a half-published day.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import httpx

from ...core.logging import get_logger
from ...core.types import Item
from ...ingest.schemas import IngestEnvelope, envelope_to_items
from .base import BaseSource, register_source

logger = get_logger(__name__)

DEFAULT_INDEX_URL = (
    "https://raw.githubusercontent.com/ElectQ/Soundwave/master/bundles/index.json"
)


class BundleFetchError(RuntimeError):
    """The bundle feed could not be read or did not verify."""


@register_source("bundle_pull")
class BundlePullSource(BaseSource):
    """Fetch a collector's published day bundles.

    Config:
        source_label: the source_id items are filed under
        index_url:    URL of bundles/index.json
        max_days:     cap on how many days one poll will backfill (default 7)
        verify_sha256: check each day against the index digest (default True)
    """

    name = "bundle_pull"
    live_fetch = False  # polled by the scheduler; a run reads the DB

    def __init__(self, **config: Any):
        super().__init__(**config)
        self.source_label = config.get("source_label", "")
        self.index_url = config.get("index_url") or DEFAULT_INDEX_URL
        self.max_days = int(config.get("max_days", 7))
        self.verify_sha256 = bool(config.get("verify_sha256", True))
        self.timeout = float(config.get("timeout", 30.0))

    async def fetch(self, since: datetime | None = None) -> list[Item]:
        since_date = since.strftime("%Y-%m-%d") if since else ""

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            index = await self._get_json(client, self.index_url)

            days = index.get("days") or []
            wanted = [d for d in days if str(d.get("date", "")) >= since_date] if since_date else days
            wanted = sorted(wanted, key=lambda d: str(d.get("date", "")))[-self.max_days :]

            if not wanted:
                logger.info(
                    "source.bundle.up_to_date",
                    source=self.source_label,
                    since=since_date,
                    latest=index.get("latest"),
                )
                return []

            items: list[Item] = []
            for day in wanted:
                items.extend(await self._fetch_day(client, day))

        logger.info(
            "source.bundle.fetched",
            source=self.source_label,
            days=[d.get("date") for d in wanted],
            count=len(items),
        )
        return items

    async def _fetch_day(self, client: httpx.AsyncClient, day: dict) -> list[Item]:
        date = str(day.get("date", ""))
        url = urljoin(self.index_url, f"{date}.json")

        resp = await client.get(url)
        if resp.status_code == 404:
            # The index listed a day the CDN has not published yet.
            logger.warning("source.bundle.day_missing", source=self.source_label, date=date)
            return []
        resp.raise_for_status()

        expected = str(day.get("sha256") or "")
        if self.verify_sha256 and expected:
            actual = hashlib.sha256(resp.content).hexdigest()
            if actual != expected:
                # index.json and <date>.json are separate CDN objects; a mismatch
                # means we read them out of step. Skip: the next poll will get a
                # consistent pair, and ingesting a torn day is worse than waiting.
                logger.warning(
                    "source.bundle.sha_mismatch",
                    source=self.source_label,
                    date=date,
                    expected=expected[:12],
                    actual=actual[:12],
                )
                return []

        try:
            env = IngestEnvelope(**resp.json())
        except Exception as e:
            raise BundleFetchError(f"bundle {date} is not a valid envelope: {e}") from e

        collect_date = env.collect_date or date
        return envelope_to_items(
            env,
            source_id=self.source_label or env.source_id or self.name,
            collect_date=collect_date,
        )

    async def _get_json(self, client: httpx.AsyncClient, url: str) -> dict:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise BundleFetchError(f"{url} did not return a JSON object")
        return data


def _utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


__all__ = ["BundlePullSource", "BundleFetchError", "DEFAULT_INDEX_URL"]
