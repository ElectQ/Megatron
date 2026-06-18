from __future__ import annotations

from datetime import datetime, timezone

from megatron.core.types import Item
from megatron.plugins.filters.base import filter_registry, run_filters


def _make_item(content="hello", like=0, rt=0, reply=0, views=100, is_rt=False):
    return Item(
        id=f"t_{abs(hash(content))}",
        source="twitter",
        source_ref="list1",
        content=content,
        url="https://x.com",
        author="user",
        author_name="User",
        published_at=datetime.now(timezone.utc),
        collected_at=datetime.now(timezone.utc),
        is_retweet=is_rt,
        metrics={
            "like_count": like,
            "retweet_count": rt,
            "reply_count": reply,
            "view_count": views,
        },
    )


def test_filters_registered():
    assert "interaction" in filter_registry
    assert "dedup" in filter_registry
    assert "keyword" in filter_registry


def test_interaction_filter_threshold():
    f = filter_registry.create("interaction", threshold=5)
    high = _make_item(like=10, rt=2, reply=1)
    low = _make_item(like=0, rt=0, reply=0)
    assert f.should_include(high)
    assert not f.should_include(low)


def test_dedup_filter_skips_retweets_and_dupes():
    f = filter_registry.create("dedup")
    original = _make_item(content="unique vuln CVE-2024-1234")
    rt = _make_item(content="unique vuln CVE-2024-1234", is_rt=True)
    dupe = _make_item(content="unique vuln CVE-2024-1234")

    assert f.should_include(original)
    assert not f.should_include(rt)
    assert not f.should_include(dupe)


def test_keyword_filter_boosts_cve():
    f = filter_registry.create("keyword")
    cve = _make_item(content="New CVE-2024-1234 affects Linux kernel")
    plain = _make_item(content="good morning everyone")
    assert f.score(cve) >= 0.8
    assert f.score(plain) < 0.5


def test_run_filters_sorts_by_score():
    interaction = filter_registry.create("interaction", threshold=1)
    items = [
        _make_item("low", like=1, views=1000),
        _make_item("high", like=50, rt=20, views=1000),
    ]
    scored = run_filters(items, [interaction])
    assert scored[0][0].content == "high"
    assert scored[1][0].content == "low"
