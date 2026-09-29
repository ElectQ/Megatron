"""Lightweight repository context for the GitHub ranking prompt.

Sentinel-gh stays a fact collector. Megatron owns analysis, so immediately before
the LLM call it reads only the beginning of each unique repository's public
README. Fetching is bounded, concurrent and best-effort: a missing or slow README
never fails the daily run.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import quote

import httpx

from ..core.logging import get_logger
from ..core.models import ItemRecord
from .github_feed import parse_github_event

logger = get_logger(__name__)

_CONCURRENCY = 6
_FETCH_BYTES = 32_000
_NORMAL_CHARS = 400
_STRONG_CHARS = 900
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_FENCE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_TAG_RE = re.compile(r"<[^>]+>")


def _kind(record: ItemRecord) -> str:
    for tag in record.tags or []:
        text = str(tag)
        if text.startswith("kind:"):
            return text.split(":", 1)[1]
    return ""


def _strong(record: ItemRecord) -> bool:
    metrics = record.metrics or {}
    try:
        circle = int(metrics.get("circle_count") or 0)
    except (TypeError, ValueError):
        circle = 0
    return bool(
        circle >= 2
        or metrics.get("trending")
        or metrics.get("trending_front_page")
        or _kind(record) in {"network_hot", "release", "created", "public_repo"}
    )


def _clean_readme(text: str, limit: int) -> str:
    text = _COMMENT_RE.sub(" ", text)
    text = _FENCE_RE.sub(" ", text)
    text = _IMAGE_RE.sub(" ", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _TAG_RE.sub(" ", text)

    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or "shields.io" in line or line.startswith("|"):
            continue
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+|>\s*)", "", line)
        if set(line) <= {"-", "=", ":", " ", "|"}:
            continue
        lines.append(line)

    cleaned = " ".join(" ".join(lines).split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rsplit(" ", 1)[0].rstrip(".,;:，。；：") + "…"


def _raw_url(repo: str) -> str:
    owner, name = repo.split("/", 1)
    return (
        "https://raw.githubusercontent.com/"
        f"{quote(owner, safe='')}/{quote(name, safe='')}/HEAD/README.md"
    )


async def _fetch_readme(client: httpx.AsyncClient, repo: str, limit: int) -> str:
    try:
        async with client.stream("GET", _raw_url(repo)) as response:
            if response.status_code != 200:
                return ""
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) >= _FETCH_BYTES:
                    break
        return _clean_readme(bytes(data[:_FETCH_BYTES]).decode("utf-8", "ignore"), limit)
    except Exception:
        return ""


_stars_cache: dict[str, int | None] = {}


async def _fetch_stars(client: httpx.AsyncClient, repo: str) -> int | None:
    """The repo's own stargazer count, for the public page. Best-effort.

    One small REST call per unique repo per day; cached in-process so re-runs
    (a retried batch, a second day mentioning the repo) don't re-hit the API.
    Anonymous rate limit is 60/h — a rate-limit reply just leaves stars unknown,
    exactly like any other failure. Never goes into the prompt.
    """
    if repo in _stars_cache:
        return _stars_cache[repo]
    try:
        response = await client.get(f"https://api.github.com/repos/{repo}")
        if response.status_code != 200:
            _stars_cache[repo] = None
            return None
        stars = int(response.json().get("stargazers_count") or 0)
        _stars_cache[repo] = stars or None
        return _stars_cache[repo]
    except Exception:
        _stars_cache[repo] = None
        return None


async def github_repo_context(records: list[ItemRecord]) -> dict[str, dict[str, str]]:
    """Return external_id -> canonical repo URL + optional README excerpt.

    A duplicate star event still needs its own verdict in the model's JSON, but
    repeating the same README four times wastes tokens. Only the first event for
    a repository carries the excerpt; every event carries the canonical URL.
    """
    grouped: dict[str, list[ItemRecord]] = {}
    repos: dict[str, str] = {}
    for record in records:
        if record.source != "github_followee_feed":
            continue
        event = parse_github_event(record)
        if event is None or not _REPO_RE.match(event.repo):
            continue
        owner, name = event.repo.split("/", 1)
        if owner in {".", ".."} or name in {".", ".."}:
            continue
        grouped.setdefault(event.repo_url, []).append(record)
        repos[event.repo_url] = event.repo

    if not grouped:
        return {}

    semaphore = asyncio.Semaphore(_CONCURRENCY)
    headers = {"User-Agent": "Megatron-GitHub-Radar/1.0", "Accept": "application/vnd.github+json"}
    timeout = httpx.Timeout(8.0, connect=5.0)
    limits = httpx.Limits(max_connections=_CONCURRENCY, max_keepalive_connections=_CONCURRENCY)

    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        headers=headers,
        follow_redirects=True,
    ) as client:

        async def fetch(url: str) -> tuple[str, str, int | None]:
            """README excerpt + the repo's own star count (None when unknown)."""
            rows = grouped[url]
            limit = _STRONG_CHARS if any(_strong(r) for r in rows) else _NORMAL_CHARS
            async with semaphore:
                excerpt = await _fetch_readme(client, repos[url], limit)
                stars = await _fetch_stars(client, repos[url])
            return url, excerpt, stars

        fetched: dict[str, tuple[str, int | None]] = {}
        for url, excerpt, stars in await asyncio.gather(*(fetch(url) for url in grouped)):
            fetched[url] = (excerpt, stars)

    context: dict[str, dict] = {}
    for url, rows in grouped.items():
        excerpt, stars = fetched.get(url, ("", None))
        for index, record in enumerate(rows):
            context[record.item_id] = {
                "repo_url": url,
                "readme_excerpt": excerpt if index == 0 else "",
            }
            if index == 0:
                context[record.item_id]["repo_stars"] = stars

    logger.info(
        "github.enrich.done",
        repos=len(grouped),
        readmes=sum(1 for text in fetched.values() if text),
        chars=sum(len(text) for text in fetched.values()),
    )
    return context


__all__ = ["github_repo_context"]
