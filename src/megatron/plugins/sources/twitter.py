from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ...core.logging import get_logger
from ...core.types import Item
from .base import BaseSource, register_source

logger = get_logger(__name__)


@register_source("twitter")
class TwitterListSource(BaseSource):
    """Parse Soundwave's data/YYYY-MM-DD/*.json format into Items.

    Config:
        data: dict payload (push mode) OR
        data_dir: path to soundwave data root (pull mode)

    Pull-mode date filtering (optional config keys):
        only_dates: set/list of date strings ("2026-06-17") to include;
                    others are skipped. Empty = include all.
        since_date: str — include only dates >= this (inclusive).
    """

    name = "twitter"

    async def fetch(self, since: datetime | None = None) -> list[Item]:
        if "data" in self.config:
            return self._parse_payload(self.config["data"], since)
        if "data_dir" in self.config:
            return self._parse_dir(self.config["data_dir"], since)
        raise ValueError("TwitterListSource needs 'data' (push) or 'data_dir' (pull)")

    def _parse_payload(self, payload: dict, since: datetime | None) -> list[Item]:
        source_ref = str(payload.get("list_id", ""))
        # 采集日 = Soundwave payload 顶层的 date 字段
        collect_date = str(payload.get("date", ""))

        # 如果配置了 only_dates/since_date,在 payload 层做快速过滤
        only_dates = self.config.get("only_dates")
        if only_dates and collect_date and collect_date not in only_dates:
            return []
        since_date = self.config.get("since_date")
        if since_date and collect_date and collect_date < since_date:
            return []

        items: list[Item] = []
        for tweet in payload.get("tweets", []):
            item = self._tweet_to_item(tweet, source_ref, collect_date)
            if since and item.published_at < since:
                continue
            items.append(item)
        logger.info(
            "source.twitter.parsed",
            source_ref=source_ref,
            collect_date=collect_date,
            count=len(items),
            mode="payload",
        )
        return items

    def _parse_dir(self, data_dir: str, since: datetime | None) -> list[Item]:
        root = Path(data_dir)
        if not root.exists():
            logger.warning("source.twitter.no_dir", path=str(root))
            return []

        only_dates = self.config.get("only_dates")
        since_date = self.config.get("since_date")

        items: list[Item] = []
        scanned_dirs = 0
        skipped_dirs = 0
        for date_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            date_str = date_dir.name
            # 日期格式校验 (YYYY-MM-DD)
            if not _is_date_str(date_str):
                continue
            # 按日期过滤
            if only_dates and date_str not in only_dates:
                skipped_dirs += 1
                continue
            if since_date and date_str < since_date:
                skipped_dirs += 1
                continue
            scanned_dirs += 1
            for path in sorted(date_dir.glob("*.json")):
                try:
                    payload = json.loads(path.read_text())
                except Exception as e:
                    logger.warning("source.twitter.bad_json", path=str(path), error=str(e))
                    continue
                items.extend(self._parse_payload(payload, since))

        logger.info(
            "source.twitter.parsed",
            count=len(items),
            mode="dir",
            path=str(root),
            scanned_dates=scanned_dirs,
            skipped_dates=skipped_dirs,
        )
        return items

    def _tweet_to_item(self, tweet: dict, source_ref: str, collect_date: str) -> Item:
        media = tweet.get("media") or {}
        return Item(
            id=str(tweet["id"]),
            source="twitter",
            source_ref=source_ref,
            content=tweet.get("content", ""),
            url=tweet.get("url", ""),
            author=tweet.get("author_handle", ""),
            author_name=tweet.get("author_name", ""),
            language=tweet.get("language", ""),
            published_at=_parse_dt(tweet.get("published_at")),
            collected_at=_parse_dt(tweet.get("collected_at")),
            collect_date=collect_date,
            is_retweet=bool(tweet.get("is_retweet")),
            is_quote=bool(tweet.get("is_quote")),
            tags=list(tweet.get("hashtags") or []),
            links=list(tweet.get("urls") or []),
            media={
                "photos": media.get("photos") or [],
                "videos": media.get("videos") or [],
                "thumbnails": media.get("thumbnails") or [],
            },
            metrics={
                "like_count": tweet.get("like_count", 0),
                "retweet_count": tweet.get("retweet_count", 0),
                "reply_count": tweet.get("reply_count", 0),
                "view_count": tweet.get("view_count", 0),
            },
            raw=tweet.get("raw") or {},
        )


def _is_date_str(s: str) -> bool:
    """Check if string matches YYYY-MM-DD."""
    if len(s) != 10 or s[4] != "-" or s[7] != "-":
        return False
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _parse_dt(value) -> datetime:
    if isinstance(value, datetime):
        return value
    if not value:
        return datetime.now(timezone.utc)
    text = str(value)
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return datetime.now(timezone.utc)


__all__ = ["TwitterListSource"]
