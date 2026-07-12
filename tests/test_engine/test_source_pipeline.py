"""Regression tests for the MCP → runner → source-binding pipeline.

Covers the fixes that made the configured MCP path real: transport routing,
fail-loud fetch, source-label alignment, pull-vs-live dispatch, task-source
validation, and the stateless test-before-save endpoint. Network-dependent
paths (real `call_tool` against GitHub) are mocked; only the wiring is asserted.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from megatron.plugins.sources.base import BaseSource, MCPSource


class _FakeACM:
    """Minimal async context manager yielding a fixed value."""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc):
        return False


def _fake_session(client):
    """Patch target for MCPSource._session: an async CM yielding ``client``."""
    return MagicMock(return_value=_FakeACM(client))


def _tool_result(payload: dict):
    """Build an object shaped like an MCP CallToolResult with JSON text."""
    return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload))])


# --------------------------------------------------------------------------- #
# BaseSource / MCPSource unit behavior
# --------------------------------------------------------------------------- #


def test_base_source_defaults():
    class Dummy(BaseSource):
        async def fetch(self, since=None):
            return []

    d = Dummy()
    assert d.live_fetch is False  # pull-based by default


@pytest.mark.asyncio
async def test_base_source_close_is_noop():
    class Dummy(BaseSource):
        async def fetch(self, since=None):
            return []

    # Non-MCP sources must tolerate the runner's unconditional close().
    assert await Dummy().close() is None


def test_mcp_source_flags_and_label():
    src = MCPSource(transport="sse", server_url="https://x/mcp", source_label="my-feed")
    assert src.live_fetch is True
    assert src._source_label == "my-feed"


def test_mcp_source_label_defaults_to_name():
    assert MCPSource()._source_label == "soundwave"


def test_tweet_item_stamped_with_source_label():
    src = MCPSource(source_label="my-feed")
    item = src._tweet_to_item(
        {
            "id": "t1",
            "content": "hi",
            "published_at": "2026-01-01 00:00:00",
            "collected_at": "2026-01-01 01:00:00",
        },
        list_id="L1",
        collect_date="2026-01-01",
    )
    assert item is not None
    assert item.source == "my-feed"  # not hardcoded "soundwave"
    assert item.collect_date == "2026-01-01"


@pytest.mark.asyncio
async def test_fetch_raises_on_error_no_swallow():
    """A protocol/connection error during fetch must raise, not return []."""
    src = MCPSource(source_label="lbl")
    client = MagicMock()
    client.call_tool = AsyncMock(side_effect=RuntimeError("boom"))
    with patch.object(src, "_session", lambda: _FakeACM(client)):
        with pytest.raises(RuntimeError, match="MCP fetch failed"):
            await src.fetch()


@pytest.mark.asyncio
async def test_fetch_empty_when_no_dates():
    """A reachable server with no data legitimately returns []."""
    src = MCPSource(source_label="lbl")
    client = MagicMock()
    client.call_tool = AsyncMock(return_value=_tool_result({"dates": []}))
    with patch.object(src, "_session", lambda: _FakeACM(client)):
        assert await src.fetch() == []


@pytest.mark.asyncio
async def test_fetch_since_filters_dates():
    src = MCPSource(source_label="lbl")

    async def fake_call(name, args):
        if name == "list_available_dates":
            return _tool_result({"dates": ["2026-01-01", "2026-01-05"]})
        return _tool_result({"tweets": []})

    client = MagicMock()
    client.call_tool = AsyncMock(side_effect=fake_call)
    with patch.object(src, "_session", lambda: _FakeACM(client)):
        from datetime import datetime, timezone

        await src.fetch(since=datetime(2026, 1, 3, tzinfo=timezone.utc))
    called_dates = [
        c.args[1]["date"] for c in client.call_tool.await_args_list if c.args[0] == "list_tweets"
    ]
    assert called_dates == ["2026-01-05"]  # 2026-01-01 filtered out by `since`


@pytest.mark.asyncio
async def test_sse_uses_positional_args_and_initialize():
    """SSE path: ClientSession(read, write) positionally + initialize() awaited."""
    session = AsyncMock()  # .initialize() is an AsyncMock
    client_session_cls = MagicMock(return_value=_FakeACM(session))
    sse_client = MagicMock(return_value=_FakeACM(("READ", "WRITE")))

    src = MCPSource(transport="sse", server_url="https://x/mcp")
    with (
        patch("mcp.ClientSession", client_session_cls),
        patch("mcp.client.sse.sse_client", sse_client),
    ):
        async with src._session() as result:
            assert result is session

    sse_client.assert_called_once_with("https://x/mcp")
    client_session_cls.assert_called_once_with("READ", "WRITE")  # positional (read, write)
    session.initialize.assert_awaited_once()


# --------------------------------------------------------------------------- #
# mcp_api._mcp_source_from_config routing
# --------------------------------------------------------------------------- #


def test_config_routing_sse():
    from megatron.web.mcp_api import _mcp_source_from_config

    s = _mcp_source_from_config("sse", "https://x/mcp", source_label="lbl")
    assert s.transport == "sse" and s.server_url == "https://x/mcp"
    assert s._source_label == "lbl"


def test_config_routing_stdio_command_line():
    from megatron.web.mcp_api import _mcp_source_from_config

    s = _mcp_source_from_config("stdio", "python -m mcp_servers.soundwave --repo o/r")
    assert s.transport == "stdio"
    assert s.command == "python"
    assert s.args == ["-m", "mcp_servers.soundwave", "--repo", "o/r"]


def test_config_routing_stdio_owner_repo():
    from megatron.web.mcp_api import _mcp_source_from_config

    s = _mcp_source_from_config("stdio", "ElectQ/Soundwave")
    assert s.transport == "stdio"
    assert not s.command  # falls through to bundled soundwave
    assert s._repo == "ElectQ/Soundwave"


# --------------------------------------------------------------------------- #
# Runner source resolution + refresh dispatch
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_refresh_noop_for_pull_source():
    """twitter (live_fetch=False) must be a no-op — never calls fetch()."""
    from megatron.core.db import async_session_factory
    from megatron.engine.runner import ModuleRunner

    async with async_session_factory() as session:
        runner = ModuleRunner(session)
        module = SimpleNamespace(source="twitter", filter_config={})
        assert await runner._refresh_data(module, run=None) is None


@pytest.mark.asyncio
async def test_resolve_source_sse_from_config():
    from megatron.core.db import async_session_factory
    from megatron.core.models import MCPServer, SourceConfig
    from megatron.core.security import encrypt_config
    from megatron.engine.runner import ModuleRunner

    async with async_session_factory() as session:
        srv = MCPServer(name="feed-sse", server_url="https://x/mcp", transport="sse")
        session.add(srv)
        await session.flush()
        session.add(
            SourceConfig(
                name="feed-sse",
                source_type="mcp",
                config=encrypt_config({"mcp_server_id": srv.id, "mcp_server_name": "feed-sse"}),
            )
        )
        await session.commit()

        runner = ModuleRunner(session)
        kind, kwargs = await runner._resolve_source(SimpleNamespace(source="feed-sse"))
        assert kind == "mcp"
        assert kwargs["source_label"] == "feed-sse"  # aligns with _select_items filter
        assert kwargs["transport"] == "sse"
        assert kwargs["server_url"] == "https://x/mcp"


@pytest.mark.asyncio
async def test_resolve_source_stdio_command_line_from_config():
    from megatron.core.db import async_session_factory
    from megatron.core.models import MCPServer, SourceConfig
    from megatron.core.security import encrypt_config
    from megatron.engine.runner import ModuleRunner

    async with async_session_factory() as session:
        srv = MCPServer(
            name="feed-stdio",
            server_url="python -m mcp_servers.soundwave --repo o/r",
            transport="stdio",
        )
        session.add(srv)
        await session.flush()
        session.add(
            SourceConfig(
                name="feed-stdio",
                source_type="mcp",
                config=encrypt_config({"mcp_server_id": srv.id}),
            )
        )
        await session.commit()

        runner = ModuleRunner(session)
        kind, kwargs = await runner._resolve_source(SimpleNamespace(source="feed-stdio"))
        assert kind == "mcp"
        assert kwargs["command"] == "python"
        assert kwargs["args"] == ["-m", "mcp_servers.soundwave", "--repo", "o/r"]


@pytest.mark.asyncio
async def test_resolve_source_builtin_twitter():
    from megatron.core.db import async_session_factory
    from megatron.engine.runner import ModuleRunner

    async with async_session_factory() as session:
        runner = ModuleRunner(session)
        kind, kwargs = await runner._resolve_source(SimpleNamespace(source="twitter"))
        assert kind == "twitter"
        assert kwargs["source_label"] == "twitter"


# --------------------------------------------------------------------------- #
# API: stateless test, name validation, task source binding
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def admin_client():
    from megatron.config import settings
    from megatron.web.app import app

    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {settings.admin_token}"}
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as c:
        yield c


@pytest.mark.asyncio
async def test_stateless_test_returns_ok_false_on_failure(admin_client):
    """Bad stdio command → {ok: false, error}, never a 500."""
    r = await admin_client.post(
        "/api/admin/mcp-servers/test",
        json={"transport": "stdio", "server_url": "/nonexistent/binary --x"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["error"]


@pytest.mark.asyncio
async def test_create_mcp_server_rejects_reserved_and_long_names(admin_client):
    for bad in ("twitter", "mcp", "x" * 33):
        r = await admin_client.post(
            "/api/admin/mcp-servers",
            json={"name": bad, "server_url": "https://x/mcp", "transport": "sse"},
        )
        assert r.status_code == 400, (bad, r.text)


@pytest.mark.asyncio
async def test_module_options_includes_configured_source(admin_client):
    # Register an MCP server → its SourceConfig should appear as a selectable source.
    await admin_client.post(
        "/api/admin/mcp-servers",
        json={"name": "opt-feed", "server_url": "https://x/mcp", "transport": "sse"},
    )
    r = await admin_client.get("/api/admin/modules/options")
    assert r.status_code == 200, r.text
    sources = r.json()["sources"]
    assert "twitter" in sources  # built-in always present
    assert "opt-feed" in sources  # configured source surfaced


@pytest.mark.asyncio
async def test_create_module_rejects_unknown_source(admin_client):
    tmpl = await admin_client.post(
        "/api/admin/prompts",
        json={"name": "src-val-tmpl", "template": "t", "output_schema": {}},
    )
    prov = await admin_client.post(
        "/api/admin/providers",
        json={"name": "src-val-prov", "model": "m", "api_key": ""},
    )
    r = await admin_client.post(
        "/api/admin/modules",
        json={
            "name": "bad-source-module",
            "source": "does-not-exist",
            "prompt_template_id": tmpl.json()["id"],
            "provider_id": prov.json()["id"],
        },
    )
    assert r.status_code == 400, r.text
