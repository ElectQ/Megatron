from __future__ import annotations

import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


from ..core.db import async_session_factory
from ..core.logging import get_logger
from ..core.models import PullState
from ..core.types import Item
from ..plugins.sources.base import source_registry
from .service import IngestService

logger = get_logger(__name__)


def _today_utc() -> str:
    """Today's date in UTC as YYYY-MM-DD (matches Soundwave's directory naming)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _dates_between(start: str, end: str) -> list[str]:
    """Inclusive list of YYYY-MM-DD strings from start to end."""
    out = []
    cur = datetime.strptime(start, "%Y-%m-%d")
    stop = datetime.strptime(end, "%Y-%m-%d")
    while cur <= stop:
        out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return out


class GitPuller:
    """Pull Soundwave data via shallow git clone with date-based filtering.

    Modes:
        auto  — read pull_state watermark, ingest dates strictly after it
                up to today. If no watermark (cold start), falls back to full.
        date  — ingest only a specific date (--date YYYY-MM-DD)
        since — ingest all dates from --since YYYY-MM-DD to today
        full  — ingest all available dates (cold start / rebuild)
    """

    def __init__(
        self,
        repo_url: str,
        source: str = "twitter",
        plugin: str = "",
        mode: str = "auto",
        target_date: str = "",
        since_date: str = "",
    ):
        self.repo_url = repo_url
        # The source_id items are filed under (and the watermark key).
        self.source = source
        # The parser for the repo's on-disk layout. Decoupled from `source` so a
        # source can be renamed without changing which plugin reads its files.
        self.plugin = plugin or "twitter"
        self.mode = mode
        self.target_date = target_date
        self.since_date = since_date

    async def run(self) -> tuple[int, int, list[str]]:
        """Returns (ingested, duplicated, dates_pulled)."""
        if not self.repo_url:
            logger.warning("puller.no_repo_url")
            return 0, 0, []

        # Compute which dates to pull based on mode + watermark
        dates_to_pull = await self._compute_dates()
        if dates_to_pull is not None and not dates_to_pull:
            logger.info("puller.nothing_to_pull", mode=self.mode)
            return 0, 0, []

        tmpdir = tempfile.mkdtemp(prefix="megatron_pull_")
        try:
            await self._git_clone(tmpdir)
            data_dir = Path(tmpdir) / "data"
            return await self._ingest_dir(data_dir, dates_to_pull)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    async def _compute_dates(self) -> list[str] | None:
        """Determine which date directories to ingest.

        Returns:
            None  → ingest ALL dates (full mode, no filter)
            []    → nothing to pull
            [...] → list of YYYY-MM-DD strings to pull
        """
        today = _today_utc()

        if self.mode == "full":
            return None

        if self.mode == "date":
            if not self.target_date:
                raise ValueError("date mode requires target_date")
            return [self.target_date]

        if self.mode == "since":
            if not self.since_date:
                raise ValueError("since mode requires since_date")
            return _dates_between(self.since_date, today)

        # auto mode: use watermark
        last_date = await self._get_watermark()
        if not last_date:
            # Cold start: no watermark → full pull
            logger.info("puller.cold_start", reason="no watermark, doing full pull")
            return None
        # Pull everything strictly after last_date up to today
        next_day = (datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)).strftime(
            "%Y-%m-%d"
        )
        if next_day > today:
            logger.info("puller.up_to_date", watermark=last_date, today=today)
            return []
        return _dates_between(next_day, today)

    async def _get_watermark(self) -> str:
        async with async_session_factory() as session:
            state = await session.get(PullState, self.source)
            return state.last_date if state else ""

    async def _update_watermark(self, latest_date: str) -> None:
        async with async_session_factory() as session:
            state = await session.get(PullState, self.source)
            now = datetime.now(timezone.utc)
            if state:
                # Only advance forward
                if not state.last_date or latest_date > state.last_date:
                    state.last_date = latest_date
                state.last_pull_at = now
            else:
                session.add(
                    PullState(
                        source=self.source,
                        last_date=latest_date,
                        last_pull_at=now,
                    )
                )
            await session.commit()

    async def _git_clone(self, dest: str) -> None:
        import asyncio

        proc = await asyncio.create_subprocess_exec(
            "git",
            "clone",
            "--depth",
            "1",
            self.repo_url,
            dest,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            logger.error("puller.clone_failed", repo=self.repo_url, error=err)
            raise RuntimeError(f"git clone failed: {err}")
        logger.info("puller.cloned", repo=self.repo_url, dest=dest)

    async def _ingest_dir(
        self, data_dir: Path, dates_to_pull: list[str] | None
    ) -> tuple[int, int, list[str]]:
        if not data_dir.exists():
            logger.warning("puller.no_data_dir", path=str(data_dir))
            return 0, 0, []

        # Build source config with date filter. `source_label` is what the items
        # get filed under — the source_id, not the plugin name.
        source_config: dict = {"data_dir": str(data_dir), "source_label": self.source}
        if dates_to_pull is not None:
            source_config["only_dates"] = set(dates_to_pull)

        plugin = source_registry.create(self.plugin, **source_config)
        items: list[Item] = await plugin.fetch()
        if not items:
            logger.info("puller.no_items", dates=dates_to_pull)
            return 0, 0, dates_to_pull or []

        # Collect the actual collect_dates present in items
        pulled_dates = sorted({it.collect_date for it in items if it.collect_date})

        async with async_session_factory() as session:
            service = IngestService(session)
            ingested, duplicated = await service.ingest_items(items, mode="pull")

        # Advance watermark to the latest date actually pulled
        if pulled_dates:
            latest = pulled_dates[-1]
            await self._update_watermark(latest)
            logger.info(
                "puller.watermark_updated",
                source=self.source,
                last_date=latest,
            )

        logger.info(
            "puller.done",
            mode=self.mode,
            dates=pulled_dates,
            ingested=ingested,
            duplicated=duplicated,
        )
        return ingested, duplicated, pulled_dates


__all__ = ["GitPuller"]
