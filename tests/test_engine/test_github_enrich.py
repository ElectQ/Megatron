from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from megatron.core.models import ItemRecord
from megatron.engine.github_enrich import _clean_readme, github_repo_context

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


def record(item_id: str, repo: str, *, circle: int = 1, action: str = "star") -> ItemRecord:
    if action == "fork":
        owner, name = repo.split("/", 1)
        url = f"https://github.com/alice/{name}"
        content = f"alice forked {repo} → alice/{name}"
    else:
        url = f"https://github.com/{repo}"
        content = f"alice starred {repo}"
    return ItemRecord(
        item_id=item_id,
        source="github_followee_feed",
        content=content,
        url=url,
        author="alice",
        published_at=NOW,
        collected_at=NOW,
        collect_date="2026-09-27",
        tags=[f"kind:{action}"],
        metrics={"circle_count": circle},
    )


def test_readme_cleanup_removes_badges_images_links_and_code():
    text = """
# Tool
[![build](https://img.shields.io/x)](https://ci.test)
![logo](logo.png)
A [Havoc](https://example.com) plugin for hidden desktop access.

```bash
pip install noise
```
## Install
Run it.
"""
    assert _clean_readme(text, 200) == "Tool A Havoc plugin for hidden desktop access. Install Run it."


@pytest.mark.asyncio
async def test_unique_repo_is_fetched_once_and_excerpt_is_not_repeated(monkeypatch):
    calls = []

    async def fake_fetch(_client, repo, limit):
        calls.append((repo, limit))
        return "真实 README 摘要"

    monkeypatch.setattr("megatron.engine.github_enrich._fetch_readme", fake_fetch)

    async def fake_stars(_client, repo):
        return 1200

    monkeypatch.setattr("megatron.engine.github_enrich._fetch_stars", fake_stars)
    rows = [record("a", "owner/tool", circle=3), record("b", "owner/tool", circle=3)]

    context = await github_repo_context(rows)

    assert calls == [("owner/tool", 900)]
    assert context["a"] == {
        "repo_url": "https://github.com/owner/tool",
        "readme_excerpt": "真实 README 摘要",
        "repo_stars": 1200,
    }
    assert context["b"]["repo_url"] == "https://github.com/owner/tool"
    assert context["b"]["readme_excerpt"] == ""
    assert "repo_stars" not in context["b"]


@pytest.mark.asyncio
async def test_fork_uses_the_source_repository_and_fetch_failure_is_safe(monkeypatch):
    async def no_readme(_client, repo, limit):
        assert repo == "bohops/UltimateWDACBypassList"
        assert limit == 400
        return ""

    monkeypatch.setattr("megatron.engine.github_enrich._fetch_readme", no_readme)

    async def no_stars(_client, repo):
        return None

    monkeypatch.setattr("megatron.engine.github_enrich._fetch_stars", no_stars)
    context = await github_repo_context(
        [record("fork", "bohops/UltimateWDACBypassList", action="fork")]
    )

    assert context["fork"] == {
        "repo_url": "https://github.com/bohops/UltimateWDACBypassList",
        "readme_excerpt": "",
        "repo_stars": None,
    }


@pytest.mark.asyncio
async def test_http_errors_return_an_empty_excerpt(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr("megatron.engine.github_enrich.httpx.AsyncClient", factory)
    context = await github_repo_context([record("x", "owner/tool")])
    assert context["x"]["readme_excerpt"] == ""
