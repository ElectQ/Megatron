from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .base import BaseSource, register_source, source_registry
from ...core.types import Item


@register_source("soundwave")
class SoundwaveSource(BaseSource):
    """Soundwave data source — fetches tweets from GitHub repository via API."""

    name = "soundwave"

    def __init__(self, **config: Any):
        super().__init__(**config)
        self.repo = config.get("repo", "ElectQ/Soundwave")
        self.branch = config.get("branch", "master")
        self._github = None

    async def _get_github(self):
        if self._github is None:
            from mcp_servers.soundwave.github_client import GitHubClient

            self._github = GitHubClient(repo=self.repo, branch=self.branch)
        return self._github

    async def fetch(self, since: datetime | None = None) -> list[Item]:
        github = await self._get_github()

        # Get available dates
        dates = await github.list_data_directories()
        if not dates:
            return []

        # Filter by since
        if since:
            since_str = since.strftime("%Y-%m-%d")
            dates = [d for d in dates if d >= since_str]

        if not dates:
            return []

        # Fetch tweets for the latest date
        latest = dates[0]
        all_tweets = await github.get_all_tweets_for_date(latest)
        items: list[Item] = []

        for list_id, tweets in all_tweets.items():
            for tweet in tweets:
                try:
                    pub = tweet.get("published_at", "")
                    if isinstance(pub, str) and pub:
                        published_at = datetime.fromisoformat(
                            pub.replace(" ", "T").replace("Z", "+00:00")
                        )
                    else:
                        published_at = datetime.now(timezone.utc)

                    col = tweet.get("collected_at", "")
                    if isinstance(col, str) and col:
                        collected_at = datetime.fromisoformat(
                            col.replace(" ", "T").replace("Z", "+00:00")
                        )
                    else:
                        collected_at = datetime.now(timezone.utc)

                    if published_at.tzinfo is None:
                        published_at = published_at.replace(tzinfo=timezone.utc)
                    if collected_at.tzinfo is None:
                        collected_at = collected_at.replace(tzinfo=timezone.utc)
                except Exception:
                    published_at = datetime.now(timezone.utc)
                    collected_at = datetime.now(timezone.utc)

                items.append(
                    Item(
                        id=str(tweet.get("id", "")),
                        source="soundwave",
                        source_ref=list_id,
                        content=tweet.get("content", ""),
                        url=tweet.get("url", ""),
                        author=tweet.get("author_handle", ""),
                        author_name=tweet.get("author_name", ""),
                        published_at=published_at,
                        collected_at=collected_at,
                        title="",
                        language="",
                        is_retweet=tweet.get("is_retweet", False),
                        is_quote=tweet.get("is_quote", False),
                        collect_date=latest,
                        tags=tweet.get("hashtags", []),
                        links=tweet.get("urls", []),
                        media=tweet.get("media", {}),
                        metrics={
                            "like_count": tweet.get("like_count", 0),
                            "retweet_count": tweet.get("retweet_count", 0),
                            "reply_count": tweet.get("reply_count", 0),
                            "view_count": tweet.get("view_count", 0),
                        },
                        raw=tweet.get("raw", {}),
                    )
                )

        return items


__all__ = ["SoundwaveSource"]
