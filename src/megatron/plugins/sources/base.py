from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from ...core.types import Item
from ...core.registry import Registry


class BaseSource(ABC):
    """Abstract data source. Subclasses implement how to get items."""

    name: str = ""

    def __init__(self, **config: Any):
        self.config = config

    @abstractmethod
    async def fetch(self, since: datetime | None = None) -> list[Item]:
        """Return normalized items published after `since` (None = all)."""
        raise NotImplementedError


class MCPSource(BaseSource):
    """MCP-based data source adapter.

    Connects to an MCP server and fetches data via MCP protocol.
    All future data sources should be accessed through this adapter.
    """

    name = "soundwave"

    def __init__(self, server_url: str = "", transport: str = "stdio", **config: Any):
        super().__init__(**config)
        self.server_url = server_url
        self.transport = transport
        self._client = None
        self._repo = config.get("repo", "ElectQ/Soundwave")
        self._branch = config.get("branch", "master")

    async def _get_client(self):
        """Lazy initialize MCP client."""
        if self._client is None:
            try:
                from mcp import Client
                from mcp.client.stdio import stdio_client

                if self.transport == "stdio":
                    # Start MCP server as subprocess
                    import sys
                    import os

                    server_path = os.path.join(
                        os.path.dirname(__file__), "..", "..", "..", "..", "mcp_servers", "soundwave"
                    )
                    server_path = os.path.abspath(server_path)

                    server_params = {
                        "command": sys.executable,
                        "args": [
                            "-m", "mcp_servers.soundwave",
                            "--repo", self._repo,
                            "--branch", self._branch,
                            "--transport", "stdio",
                        ],
                        "env": None,
                    }

                    read_stream, write_stream = await stdio_client(server_params)
                    self._client = Client(read_stream, write_stream)
                    await self._client.initialize()
                else:
                    # SSE mode - connect to remote server
                    self._client = Client(self.server_url)
                    await self._client.connect()
            except ImportError:
                # Fallback if mcp package is not available
                self._client = None
                raise RuntimeError("mcp package not installed. Run: uv add mcp>=1.0.0")

        return self._client

    async def fetch(self, since: datetime | None = None) -> list[Item]:
        """Fetch items from MCP server.

        Uses MCP tools to get data and normalizes to Item format.
        """
        client = await self._get_client()

        if client is None:
            return []

        try:
            # Get available dates
            dates_result = await client.call_tool("list_available_dates", {})
            dates_data = self._parse_tool_result(dates_result)
            available_dates = dates_data.get("dates", [])

            if not available_dates:
                return []

            # Filter dates if since is provided
            if since:
                since_str = since.strftime("%Y-%m-%d")
                available_dates = [d for d in available_dates if d >= since_str]

            if not available_dates:
                return []

            # Get tweets for the latest date (or all dates since)
            items = []
            for date in available_dates[:5]:  # Limit to 5 most recent dates
                result = await client.call_tool("list_tweets", {"date": date})
                data = self._parse_tool_result(result)

                if "lists" in data:
                    # Multiple lists
                    for list_id, list_data in data["lists"].items():
                        for tweet in list_data.get("tweets", []):
                            item = self._tweet_to_item(tweet, list_id)
                            if item:
                                items.append(item)
                elif "tweets" in data:
                    # Single list
                    for tweet in data["tweets"]:
                        item = self._tweet_to_item(tweet, data.get("list_id", "unknown"))
                        if item:
                            items.append(item)

            return items

        except Exception as e:
            # Log error and return empty list
            import logging
            logging.getLogger(__name__).error(f"MCP fetch failed: {e}")
            return []

    async def discover_capabilities(self) -> dict:
        """Discover what tools the MCP server provides."""
        client = await self._get_client()

        if client is None:
            return {}

        try:
            tools = await client.list_tools()
            return {
                "tools": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                    }
                    for tool in tools
                ]
            }
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Capability discovery failed: {e}")
            return {}

    def _parse_tool_result(self, result) -> dict:
        """Parse MCP tool result to dict."""
        if isinstance(result, list) and len(result) > 0:
            content = result[0]
            if hasattr(content, "text"):
                import json
                return json.loads(content.text)
        return {}

    def _tweet_to_item(self, tweet: dict, list_id: str) -> Item | None:
        """Convert Soundwave tweet format to Megatron Item."""
        try:
            from datetime import timezone

            published_at = datetime.fromisoformat(tweet.get("published_at", "").replace(" ", "T"))
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)

            collected_at = datetime.fromisoformat(tweet.get("collected_at", "").replace(" ", "T"))
            if collected_at.tzinfo is None:
                collected_at = collected_at.replace(tzinfo=timezone.utc)

            # Extract collect_date from published_at or use current date
            collect_date = published_at.strftime("%Y-%m-%d")

            return Item(
                id=tweet.get("id", ""),
                source="soundwave",
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
