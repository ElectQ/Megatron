from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from ...core.types import Item
from ...core.registry import Registry


class BaseSource(ABC):
    """Abstract data source. Subclasses implement how to get items."""

    name: str = ""

    #: Whether the runner should fetch from this source inline during a run.
    #: Pull-based sources (e.g. git) are ingested out-of-band by the scheduler,
    #: so their inline refresh is a no-op. Live sources (MCP) fetch on demand
    #: and their failures must surface as a failed run.
    live_fetch: bool = False

    def __init__(self, **config: Any):
        self.config = config

    @abstractmethod
    async def fetch(self, since: datetime | None = None) -> list[Item]:
        """Return normalized items published after `since` (None = all)."""
        raise NotImplementedError

    async def close(self) -> None:
        """Release any resources held by the source. No-op by default."""
        return None


class MCPSource(BaseSource):
    """MCP-based data source adapter.

    Connects to an MCP server (stdio subprocess or SSE endpoint) and fetches
    data via the MCP protocol. New data sources should be reachable through
    this adapter by pointing it at their MCP server.
    """

    name = "soundwave"
    live_fetch = True

    def __init__(
        self,
        server_url: str = "",
        transport: str = "stdio",
        command: str = "",
        args: list[str] | None = None,
        source_label: str = "",
        **config: Any,
    ):
        super().__init__(**config)
        self.server_url = server_url
        self.transport = transport
        self.command = command
        self.args = args or []
        # Label to stamp on ingested items so the runner's source filter matches.
        self._source_label = source_label or self.name
        # Bundled-soundwave fallback (only used when no command is configured).
        self._repo = config.get("repo", "ElectQ/Soundwave")
        self._branch = config.get("branch", "master")

    @asynccontextmanager
    async def _session(self):
        """Open an initialized MCP ClientSession for the configured transport.

        The whole connection (transport + session) lives inside this single
        ``async with`` so its anyio cancel scopes are entered and exited in the
        same task. Holding the streams open via an ``AsyncExitStack`` and closing
        later trips "cancel scope in a different task" — hence per-operation
        scoping, and ``close()`` is a no-op.
        """
        try:
            from mcp import ClientSession, StdioServerParameters, stdio_client
        except ImportError as e:
            raise RuntimeError("mcp package not installed. Run: uv add 'mcp>=1.0.0'") from e

        if self.transport == "stdio":
            if self.command:
                server_params = StdioServerParameters(command=self.command, args=self.args)
            else:
                # Fallback: the bundled Soundwave MCP server.
                import sys

                server_params = StdioServerParameters(
                    command=sys.executable,
                    args=[
                        "-m", "mcp_servers.soundwave",
                        "--repo", self._repo,
                        "--branch", self._branch,
                        "--transport", "stdio",
                    ],
                )
            async with stdio_client(server_params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    yield session
        elif self.transport == "sse":
            from mcp.client.sse import sse_client

            if not self.server_url:
                raise RuntimeError("SSE transport requires a server_url")
            async with sse_client(self.server_url) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    yield session
        else:
            raise RuntimeError(f"Unsupported MCP transport: {self.transport}")

    async def fetch(self, since: datetime | None = None) -> list[Item]:
        """Fetch items from the MCP server.

        Raises on connection/protocol/parse failure so the run is marked failed.
        A reachable server with no new data legitimately returns ``[]``.
        """
        try:
            async with self._session() as client:
                dates_result = await client.call_tool("list_available_dates", {})
                dates_data = self._parse_tool_result(dates_result)
                available_dates = sorted(dates_data.get("dates", []))

                if since:
                    since_str = since.strftime("%Y-%m-%d")
                    available_dates = [d for d in available_dates if d >= since_str]

                if not available_dates:
                    return []

                items: list[Item] = []
                for date in available_dates:
                    result = await client.call_tool("list_tweets", {"date": date})
                    data = self._parse_tool_result(result)

                    if "lists" in data:
                        for list_id, list_data in data["lists"].items():
                            for tweet in list_data.get("tweets", []):
                                item = self._tweet_to_item(tweet, list_id, date)
                                if item:
                                    items.append(item)
                    elif "tweets" in data:
                        for tweet in data["tweets"]:
                            item = self._tweet_to_item(tweet, data.get("list_id", "unknown"), date)
                            if item:
                                items.append(item)

                return items
        except Exception as e:
            raise RuntimeError(f"MCP fetch failed ({self._source_label}): {e}") from e

    async def discover_capabilities(self) -> dict:
        """Enumerate the tools the MCP server exposes (real protocol query)."""
        async with self._session() as client:
            result = await client.list_tools()
            return {
                "tools": [
                    {"name": t.name, "description": t.description}
                    for t in result.tools
                ]
            }

    def _parse_tool_result(self, result) -> dict:
        """Parse an MCP tool result into a dict."""
        if hasattr(result, "content") and result.content:
            content = result.content[0]
            if hasattr(content, "text"):
                import json

                return json.loads(content.text)
        return {}

    def _tweet_to_item(self, tweet: dict, list_id: str, collect_date: str) -> Item | None:
        """Convert a Soundwave tweet payload to a Megatron Item.

        ``collect_date`` is the collection day the tweet was fetched under (the
        ``list_tweets`` request date), matching the twitter/git-pull semantics so
        the runner's collect_date filter behaves consistently across ingest paths.
        """
        try:
            from datetime import timezone

            published_at = datetime.fromisoformat(tweet.get("published_at", "").replace(" ", "T"))
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)

            collected_at = datetime.fromisoformat(tweet.get("collected_at", "").replace(" ", "T"))
            if collected_at.tzinfo is None:
                collected_at = collected_at.replace(tzinfo=timezone.utc)

            return Item(
                id=tweet.get("id", ""),
                source=self._source_label,
                source_ref=list_id,
                content=tweet.get("content", ""),
                url=tweet.get("url", ""),
                author=tweet.get("author_handle", ""),
                published_at=published_at,
                collected_at=collected_at,
                title="",  # Tweets don't have titles
                author_name=tweet.get("author_name", ""),
                language="",  # Could be detected
                is_retweet=tweet.get("is_retweet", False),
                is_quote=tweet.get("is_quote", False),
                collect_date=collect_date,
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
        except Exception as e:
            import logging

            logging.getLogger(__name__).error(f"Failed to convert tweet to item: {e}")
            return None


source_registry: Registry[BaseSource] = Registry(kind="source")


def register_source(name: str):
    return source_registry.register(name)


# Register built-in sources
source_registry.register("soundwave")(MCPSource)
source_registry.register("mcp")(MCPSource)


__all__ = ["BaseSource", "MCPSource", "source_registry", "register_source"]
