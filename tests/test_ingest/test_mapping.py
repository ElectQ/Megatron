from __future__ import annotations

from datetime import datetime, timezone

import pytest

from megatron.ingest.spec import MapSpec
from megatron.plugins.sources.mapping import (
    MappingError,
    extract_items,
    map_payload,
    parse_dt,
    resolve_path,
)


def test_resolve_root():
    assert resolve_path({"a": 1}, "$") == {"a": 1}


def test_resolve_nested_key():
    assert resolve_path({"a": {"b": {"c": 7}}}, "$.a.b.c") == 7


def test_resolve_list_index():
    assert resolve_path({"a": [{"b": 1}, {"b": 2}]}, "$.a[1].b") == 2


def test_resolve_wildcard_projects_key():
    assert resolve_path({"a": [{"b": 1}, {"b": 2}]}, "$.a[*].b") == [1, 2]


def test_missing_path_is_none_not_error():
    assert resolve_path({"a": 1}, "$.nope.deeper") is None


def test_non_dollar_expression_is_a_constant():
    assert resolve_path({"a": 1}, "literal") == "literal"


def test_extract_items_requires_a_list():
    with pytest.raises(MappingError, match="must resolve to a list"):
        extract_items({"hits": {"not": "a list"}}, MapSpec(items="$.hits"))


def test_extract_items_reports_the_expression_that_missed():
    with pytest.raises(MappingError, match=r"map.items '\$\.nope'"):
        extract_items({"hits": []}, MapSpec(items="$.nope"))


def test_map_payload_builds_items():
    payload = {
        "hits": [
            {
                "objectID": "42",
                "title": "A bug",
                "url": "https://example.com/1",
                "author": "alice",
                "created_at": "2026-07-12T08:00:00Z",
                "points": 120,
                "num_comments": 9,
            }
        ]
    }
    spec = MapSpec(
        items="$.hits",
        external_id="$.objectID",
        title="$.title",
        url="$.url",
        author="$.author",
        published_at="$.created_at",
        metrics={"like_count": "$.points", "reply_count": "$.num_comments"},
    )

    items = map_payload(payload, spec, source_id="hn", collect_date="2026-07-12")

    assert len(items) == 1
    it = items[0]
    assert it.id == "42"
    assert it.source == "hn"  # source_id, not the plugin kind
    assert it.title == "A bug"
    assert it.url == "https://example.com/1"
    assert it.metrics == {"like_count": 120, "reply_count": 9}
    assert it.collect_date == "2026-07-12"
    assert it.raw["objectID"] == "42"


def test_map_payload_skips_items_without_external_id():
    payload = {"hits": [{"objectID": "1"}, {"no_id": True}, {"objectID": ""}]}
    items = map_payload(
        payload,
        MapSpec(items="$.hits", external_id="$.objectID"),
        source_id="hn",
        collect_date="2026-07-12",
    )
    assert [i.id for i in items] == ["1"]


def test_parse_dt_handles_iso_epoch_and_rfc822():
    assert parse_dt("2026-07-12T08:00:00Z") == datetime(2026, 7, 12, 8, 0, tzinfo=timezone.utc)
    assert parse_dt(1_752_307_200).tzinfo is timezone.utc
    assert parse_dt("Sat, 12 Jul 2026 08:00:00 +0000").year == 2026


def test_parse_dt_falls_back_to_now_on_garbage():
    before = datetime.now(timezone.utc)
    assert parse_dt("not a date") >= before
