"""The adapter — not the plugin class — decides whether a run fetches.

The headline guarantee: an analysis run reads `items` from the database. It does
not pull a day over MCP and analyse it, which is what the spec rejects.
"""

from __future__ import annotations

import pytest

from megatron.core.db import async_session_factory
from megatron.engine.runner import ModuleRunner
from megatron.ingest.registry import sync_specs
from megatron.ingest.spec import SourceSpec


class _Module:
    """Just enough of AnalysisModule for the source-binding path."""

    def __init__(self, source: str):
        self.source = source
        self.source_ref = ""
        self.name = "test_module"
        self.filter_config = {}


async def _register(*specs: SourceSpec):
    async with async_session_factory() as session:
        await sync_specs(session, list(specs))


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ["http_push", "http_pull", "bundle_pull", "git_pull"])
async def test_out_of_band_adapters_never_fetch_inside_a_run(adapter, monkeypatch):
    spec_kwargs = {"source_id": "s_test", "adapter": adapter}
    if adapter == "http_pull":
        spec_kwargs |= {
            "fetch": {"format": "json", "url": "https://x"},
            "map": {"items": "$", "external_id": "$.id"},
        }
    elif adapter == "bundle_pull":
        spec_kwargs |= {"config": {"index_url": "https://x/index.json"}}
    elif adapter == "git_pull":
        spec_kwargs |= {"config": {"repo_url": "https://github.com/x/y"}}

    await _register(SourceSpec(**spec_kwargs))

    async def _boom(*a, **kw):
        raise AssertionError(f"adapter={adapter} must not fetch during a run")

    monkeypatch.setattr(ModuleRunner, "_mcp_backfill", _boom)

    async with async_session_factory() as session:
        runner = ModuleRunner(session)
        runner._warnings = []
        assert await runner._refresh_data(_Module("s_test"), run=None) is None


@pytest.mark.asyncio
async def test_mcp_query_with_live_fetch_off_warns_instead_of_fetching(monkeypatch):
    from megatron.config import get_ingest_settings

    await _register(SourceSpec(source_id="mcp_src", adapter="mcp_query"))
    monkeypatch.setenv("MEGATRON_MCP_LIVE_FETCH", "off")
    get_ingest_settings.cache_clear()

    async with async_session_factory() as session:
        runner = ModuleRunner(session)
        runner._warnings = []
        result = await runner._refresh_data(_Module("mcp_src"), run=None)

    assert result is None
    assert runner._warnings
    assert runner._warnings[0]["code"] == "mcp_live_fetch_disabled"
    get_ingest_settings.cache_clear()


@pytest.mark.asyncio
async def test_backfill_is_skipped_when_the_day_already_has_rows(monkeypatch):
    """The collector delivered — do not touch the network."""
    from datetime import datetime, timezone

    from megatron.config import get_ingest_settings
    from megatron.core.models import ItemRecord
    from megatron.ingest.service import IngestService
    from megatron.core.types import Item

    await _register(SourceSpec(source_id="mcp_src", adapter="mcp_query"))
    monkeypatch.setenv("MEGATRON_MCP_LIVE_FETCH", "backfill")
    get_ingest_settings.cache_clear()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        await IngestService(session).ingest_items(
            [
                Item(
                    id="already-here",
                    source="mcp_src",
                    source_ref="",
                    content="c",
                    url="https://x/1",
                    author="a",
                    published_at=now,
                    collected_at=now,
                    collect_date=today,
                )
            ]
        )

    async def _boom(*a, **kw):
        raise AssertionError("must not fetch: the day already has rows")

    async with async_session_factory() as session:
        runner = ModuleRunner(session)
        runner._warnings = []
        monkeypatch.setattr(
            "megatron.plugins.sources.base.MCPSource.fetch", _boom, raising=False
        )
        assert await runner._refresh_data(_Module("mcp_src"), run=None) is None

    # Nothing was backfilled, and no warning was raised: this is the happy path.
    assert not runner._warnings
    _ = ItemRecord  # keep the import meaningful for readers
    get_ingest_settings.cache_clear()


@pytest.mark.asyncio
async def test_binding_reports_the_adapter_from_the_registry():
    await _register(
        SourceSpec(
            source_id="bundle_src",
            adapter="bundle_pull",
            config={"index_url": "https://x/index.json"},
        )
    )
    async with async_session_factory() as session:
        binding = await ModuleRunner(session)._source_binding(_Module("bundle_src"))

    assert binding.adapter == "bundle_pull"
    assert binding.kind == "bundle_pull"
    assert binding.kwargs["index_url"] == "https://x/index.json"
    assert binding.source_id == "bundle_src"


@pytest.mark.asyncio
async def test_unregistered_source_falls_back_to_the_legacy_kind():
    """An install that has not run `sources sync` behaves exactly as before."""
    async with async_session_factory() as session:
        binding = await ModuleRunner(session)._source_binding(_Module("soundwave"))
    assert binding.adapter == "mcp_query"
