from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .github_client import GitHubClient


def create_soundwave_server(repo: str = "ElectQ/Soundwave", branch: str = "master") -> Server:
    """Create and configure the Soundwave MCP Server."""
    server = Server("soundwave")
    client = GitHubClient(repo=repo, branch=branch)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="list_tweets",
                description="Get tweets for a specific date and Twitter list",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "date": {
                            "type": "string",
                            "description": "Date in YYYY-MM-DD format",
                        },
                        "list_id": {
                            "type": "string",
                            "description": "Twitter list ID (optional, gets all lists if omitted)",
                        },
                    },
                    "required": ["date"],
                },
            ),
            Tool(
                name="search_tweets",
                description="Search tweets by keyword across a date range",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query (searches in content, author, hashtags)",
                        },
                        "start_date": {
                            "type": "string",
                            "description": "Start date in YYYY-MM-DD format (optional)",
                        },
                        "end_date": {
                            "type": "string",
                            "description": "End date in YYYY-MM-DD format (optional, defaults to today)",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of results (default: 100)",
                            "default": 100,
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="get_stats",
                description="Get crawl statistics for the last N days",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "days": {
                            "type": "integer",
                            "description": "Number of days to look back (default: 30)",
                            "default": 30,
                        },
                    },
                },
            ),
            Tool(
                name="list_available_dates",
                description="List all available dates with data",
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        if name == "list_tweets":
            date = arguments["date"]
            list_id = arguments.get("list_id")

            if list_id:
                tweets = await client.get_tweets_for_list(date, list_id)
                result = {
                    "date": date,
                    "list_id": list_id,
                    "count": len(tweets),
                    "tweets": tweets,
                }
            else:
                all_tweets = await client.get_all_tweets_for_date(date)
                total = sum(len(tweets) for tweets in all_tweets.values())
                result = {
                    "date": date,
                    "count": total,
                    "lists": {
                        list_id: {
                            "count": len(tweets),
                            "tweets": tweets,
                        }
                        for list_id, tweets in all_tweets.items()
                    },
                }

            return [TextContent(type="text", text=__import__("json").dumps(result, ensure_ascii=False, indent=2, default=str))]

        elif name == "search_tweets":
            query = arguments["query"]
            start_date = arguments.get("start_date")
            end_date = arguments.get("end_date")
            max_results = arguments.get("max_results", 100)

            date_range = None
            if start_date and end_date:
                date_range = (start_date, end_date)

            tweets = await client.search_tweets(query, date_range, max_results)
            result = {
                "query": query,
                "count": len(tweets),
                "tweets": tweets,
            }

            return [TextContent(type="text", text=__import__("json").dumps(result, ensure_ascii=False, indent=2, default=str))]

        elif name == "get_stats":
            days = arguments.get("days", 30)
            stats = await client.get_stats(days)
            return [TextContent(type="text", text=__import__("json").dumps(stats, ensure_ascii=False, indent=2, default=str))]

        elif name == "list_available_dates":
            dates = await client.list_data_directories()
            result = {
                "count": len(dates),
                "dates": dates,
                "latest": dates[0] if dates else None,
            }
            return [TextContent(type="text", text=__import__("json").dumps(result, ensure_ascii=False, indent=2, default=str))]

        else:
            raise ValueError(f"Unknown tool: {name}")

    return server


async def run_stdio_server(repo: str = "ElectQ/Soundwave", branch: str = "main") -> None:
    """Run the MCP server using stdio transport."""
    server = create_soundwave_server(repo=repo, branch=branch)
    async with stdio_server(server) as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


__all__ = ["create_soundwave_server", "run_stdio_server"]
