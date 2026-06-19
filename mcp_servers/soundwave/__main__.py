from __future__ import annotations

import argparse
import asyncio
import sys

from .server import run_stdio_server


def main():
    parser = argparse.ArgumentParser(description="Soundwave MCP Server")
    parser.add_argument(
        "--repo",
        default="ElectQ/Soundwave",
        help="GitHub repository (default: ElectQ/Soundwave)",
    )
    parser.add_argument(
        "--branch",
        default="master",
        help="Git branch (default: master)",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport mode (default: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=3000,
        help="Port for SSE mode (default: 3000)",
    )

    args = parser.parse_args()

    if args.transport == "stdio":
        asyncio.run(run_stdio_server(repo=args.repo, branch=args.branch))
    else:
        print("SSE mode not yet implemented", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
