from __future__ import annotations

import pytest

from megatron.core.db import async_session_factory
from megatron.ingest.registry import (
    get_source,
    list_sources,
    load_specs,
    resolve_source_id,
    sync_from_dir,
    sync_specs,
    to_api,
)
from megatron.ingest.spec import SourceSpec

PUSH_YAML = """
source_id: twitter_security_list
display_name: Twitter 安全 List
kind: twitter_list
adapter: http_push
audience: [personal]
schedule_expect:
  timezone: Asia/Shanghai
  collect_by: "06:00"
  sla_minutes: 90
config:
  legacy_aliases: [soundwave, twitter]
"""

PULL_YAML = """
source_id: hn_frontpage
adapter: http_pull
schedule:
  cron: "0 6 * * *"
fetch:
  format: json
  url: https://example.com/api
map:
  items: $.hits
  external_id: $.objectID
"""


@pytest.fixture
def sources_dir(tmp_path):
    (tmp_path / "twitter_security_list.yaml").write_text(PUSH_YAML)
    (tmp_path / "hn_frontpage.yaml").write_text(PULL_YAML)
    return tmp_path


# ------------------------------------------------------------------ spec parsing


def test_slug_rejects_uppercase_and_overlong():
    with pytest.raises(ValueError, match="source_id"):
        SourceSpec(source_id="NotASlug")
    with pytest.raises(ValueError, match="source_id"):
        SourceSpec(source_id="x" * 40)


def test_http_pull_without_fetch_is_rejected():
    with pytest.raises(ValueError, match="requires a `fetch:` block"):
        SourceSpec(source_id="broken", adapter="http_pull")


def test_http_pull_json_without_map_is_rejected():
    with pytest.raises(ValueError, match="requires a `map:` block"):
        SourceSpec(
            source_id="broken",
            adapter="http_pull",
            fetch={"format": "json", "url": "https://x"},
        )


def test_rss_needs_no_map():
    spec = SourceSpec(
        source_id="blog_rss",
        adapter="http_pull",
        fetch={"format": "rss", "url": "https://x/feed.xml"},
    )
    assert spec.map is None


def test_audience_scalar_collapses_both():
    assert SourceSpec(source_id="a_src", audience=["personal"]).audience_scalar == "personal"
    assert SourceSpec(source_id="b_src", audience=["personal", "public"]).audience_scalar == "both"


def test_env_interpolation_keeps_secret_out_of_the_file(monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "s3cret")
    spec = SourceSpec(
        source_id="api_src",
        adapter="http_pull",
        fetch={
            "format": "json",
            "url": "https://x",
            "headers": {"Authorization": "Bearer ${MY_TOKEN}"},
        },
        map={"items": "$", "external_id": "$.id"},
    )
    assert spec.fetch.headers["Authorization"] == "Bearer ${MY_TOKEN}"  # file keeps the ref
    assert spec.fetch.resolved_headers()["Authorization"] == "Bearer s3cret"


# ------------------------------------------------------------------ YAML loading


def test_load_specs_reads_a_directory(sources_dir):
    specs, errors = load_specs(sources_dir)
    assert errors == []
    assert {s.source_id for s in specs} == {"twitter_security_list", "hn_frontpage"}


def test_one_broken_file_does_not_sink_the_others(sources_dir):
    (sources_dir / "broken.yaml").write_text("source_id: BAD UPPER\nadapter: nope\n")
    specs, errors = load_specs(sources_dir)
    assert len(errors) == 1
    assert "broken.yaml" in str(errors[0])
    assert {s.source_id for s in specs} == {"twitter_security_list", "hn_frontpage"}


def test_missing_dir_is_not_an_error(tmp_path):
    specs, errors = load_specs(tmp_path / "does-not-exist")
    assert (specs, errors) == ([], [])


def test_duplicate_source_id_is_reported(sources_dir):
    (sources_dir / "dupe.yaml").write_text(PUSH_YAML)
    _, errors = load_specs(sources_dir)
    assert any("duplicate source_id" in str(e) for e in errors)


# ---------------------------------------------------------------- DB projection


@pytest.mark.asyncio
async def test_sync_creates_then_updates_idempotently(sources_dir):
    async with async_session_factory() as session:
        first = await sync_from_dir(session, sources_dir)
        assert first["created"] == 2

        second = await sync_from_dir(session, sources_dir)
        assert second["created"] == 0
        assert second["updated"] == 2

        sc = await get_source(session, "twitter_security_list")
        assert sc.adapter == "http_push"
        assert sc.managed_by == "yaml"
        assert sc.audience == "personal"
        assert sc.schedule_expect["collect_by"] == "06:00"


@pytest.mark.asyncio
async def test_http_pull_fetch_and_map_land_in_config(sources_dir):
    async with async_session_factory() as session:
        await sync_from_dir(session, sources_dir)
        sc = await get_source(session, "hn_frontpage")
        assert sc.adapter == "http_pull"
        assert sc.config["fetch"]["url"] == "https://example.com/api"
        assert sc.config["map"]["items"] == "$.hits"
        assert sc.config["cron"] == "0 6 * * *"


@pytest.mark.asyncio
async def test_removed_spec_is_disabled_not_deleted(sources_dir):
    async with async_session_factory() as session:
        await sync_from_dir(session, sources_dir)
        (sources_dir / "hn_frontpage.yaml").unlink()

        result = await sync_from_dir(session, sources_dir)
        assert result["disabled"] == 1

        sc = await get_source(session, "hn_frontpage")
        assert sc is not None, "row must survive: items still reference this source label"
        assert sc.enabled is False


@pytest.mark.asyncio
async def test_resolve_by_legacy_alias(sources_dir):
    async with async_session_factory() as session:
        await sync_from_dir(session, sources_dir)

        sc = await resolve_source_id(session, "soundwave")
        assert sc is not None and sc.name == "twitter_security_list"

        assert await resolve_source_id(session, "nope") is None


@pytest.mark.asyncio
async def test_list_sources_filters_by_adapter(sources_dir):
    async with async_session_factory() as session:
        await sync_from_dir(session, sources_dir)
        pulls = await list_sources(session, adapter="http_pull")
        assert [s.name for s in pulls] == ["hn_frontpage"]


@pytest.mark.asyncio
async def test_to_api_returns_audience_as_a_list_and_masks_headers():
    spec = SourceSpec(
        source_id="api_src",
        adapter="http_pull",
        fetch={
            "format": "json",
            "url": "https://x",
            "headers": {"Authorization": "Bearer ${T}"},
        },
        map={"items": "$", "external_id": "$.id"},
    )
    async with async_session_factory() as session:
        await sync_specs(session, [spec])
        sc = await get_source(session, "api_src")

    body = to_api(sc)
    assert body["audience"] == ["personal"]
    assert body["config"]["fetch"]["headers"] == {"Authorization": "***"}
