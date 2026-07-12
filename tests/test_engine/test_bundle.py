"""Caps are enforced by code, not by asking the model nicely."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from megatron.core.models import ItemRecord
from megatron.engine.bundle import (
    build_day_bundle,
    enforce_caps,
    push_items,
)
from megatron.engine.doorbell import MAX_DIGEST_CHARS, render_digest

# Representative caps + politics list. The engine's DEFAULT_CAPS/POLITICS are now a
# NEUTRAL fallback (the real values live in config/policy.yaml), so these mechanism
# tests supply their own inputs instead of leaning on framework constants.
DEFAULT_CAPS = {
    "lead_min": 3,
    "must_see_min": 5,
    "must_see_max": 8,
    "recommend_max": 15,
    "skim_max": 0,
}
POLITICS = ("政治", "大选", "选举", "地缘", "白宫", "制裁", "战争")


def rec(item_id: str, source: str = "src_a", url: str = "") -> ItemRecord:
    now = datetime.now(timezone.utc)
    return ItemRecord(
        id=int(item_id),
        item_id=f"ext-{item_id}",
        source=source,
        url=url or f"https://example.com/{item_id}",
        content=f"content {item_id}",
        author="alice",
        author_name="Alice",
        published_at=now,
        collected_at=now,
        collect_date="2026-07-12",
        metrics={"like_count": 5},
    )


def llm_item(n: str, tier: str, relevance: int = 2, source: str = "src_a") -> dict:
    return {
        "external_id": f"ext-{n}",
        "source_id": source,
        "tier": tier,
        "one_liner": f"thing {n} happened",
        "why_for_me": f"because {n}",
        "scores": {"relevance": relevance, "actionability": 2, "confidence": 0.8},
    }


# ------------------------------------------------------------------- caps


def test_the_push_carries_must_see_and_recommend():
    items = [
        {"id": 1, "tier": "must_see_push", "scores": {}},
        {"id": 2, "tier": "must_see_page", "scores": {}},
        {"id": 3, "tier": "recommend", "scores": {}},
        {"id": 4, "tier": "skim", "scores": {}},
    ]
    _, push_ids = enforce_caps(items, {**DEFAULT_CAPS, "lead_min": 0, "must_see_min": 0})

    assert push_ids == ["1", "2", "3"], "速览 stays on the page"


def test_the_lead_is_a_floor_not_a_ceiling():
    """最少三条打头，不是最多三条: eight urgent items all stay urgent."""
    items = [{"id": i, "tier": "must_see_push", "scores": {"relevance": i}} for i in range(8)]
    kept, push_ids = enforce_caps(items, DEFAULT_CAPS)

    lead = [i for i in kept if i["tier"] == "must_see_push"]
    assert len(lead) == 8, "nothing is demoted just for being the fourth urgent thing"
    assert len(push_ids) == 8


def test_a_cautious_model_still_gets_a_headline():
    """Zero must_see_push would open the message with no lead at all."""
    items = [{"id": i, "tier": "must_see_page", "scores": {"relevance": i}} for i in range(6)]
    kept, _ = enforce_caps(items, DEFAULT_CAPS)  # lead_min = 3

    lead = [i for i in kept if i["tier"] == "must_see_push"]
    assert sorted(i["id"] for i in lead) == [3, 4, 5], "the best of 必看 leads"
    assert all(i.get("promoted_from") == "must_see_page" for i in lead)


def test_the_lead_floor_cannot_invent_items_that_are_not_there():
    items = [{"id": 1, "tier": "must_see_page", "scores": {}}]
    kept, push_ids = enforce_caps(items, DEFAULT_CAPS)
    assert len(push_ids) == 1


def test_must_see_has_a_ceiling_across_both_tiers():
    """必看 = must_see_push + must_see_page, capped at must_see_max."""
    items = [{"id": i, "tier": "must_see_page", "scores": {"relevance": i}} for i in range(12)]
    kept, _ = enforce_caps(items, DEFAULT_CAPS)  # must_see_max = 8

    must_see = [i for i in kept if i["tier"].startswith("must_see")]
    assert len(must_see) == 8
    assert all(i["tier"] == "recommend" for i in kept if i not in must_see)


def test_a_stingy_model_still_gets_a_full_must_see_section():
    """A ceiling alone lets the model hand back an empty first screen."""
    items = [{"id": i, "tier": "recommend", "scores": {"relevance": i % 4}} for i in range(20)]
    kept, _ = enforce_caps(items, DEFAULT_CAPS)  # must_see_min = 5

    must_see = [i for i in kept if i["tier"].startswith("must_see")]
    assert len(must_see) == 5
    assert all(i.get("promoted_from") for i in must_see)


def test_the_floor_promotes_the_best_recommendations():
    items = [{"id": i, "tier": "recommend", "scores": {"relevance": i}} for i in range(10)]
    kept, _ = enforce_caps(items, {**DEFAULT_CAPS, "must_see_min": 3, "lead_min": 0})

    promoted = sorted(i["id"] for i in kept if i["tier"] == "must_see_page")
    assert promoted == [7, 8, 9]


def test_the_floor_never_promotes_skim():
    """skim means "a glance is enough"; calling it 必看 would be a lie."""
    items = [{"id": i, "tier": "skim", "scores": {}} for i in range(10)]
    kept, push_ids = enforce_caps(items, DEFAULT_CAPS)

    assert all(i["tier"] == "skim" for i in kept)
    assert push_ids == [], "a day of nothing-much pushes nothing"


def test_recommend_is_capped_because_it_is_delivered():
    items = [{"id": i, "tier": "recommend", "scores": {"relevance": i}} for i in range(30)]
    kept, _ = enforce_caps(items, {**DEFAULT_CAPS, "must_see_min": 0, "lead_min": 0})

    assert len([i for i in kept if i["tier"] == "recommend"]) == 15
    assert len([i for i in kept if i["tier"] == "skim"]) == 15, "demoted to the page, not dropped"


def test_skim_is_unbounded_so_nothing_falls_off_the_end():
    items = [{"id": i, "tier": "skim", "scores": {}} for i in range(200)]
    kept, _ = enforce_caps(items, DEFAULT_CAPS)  # skim_max = 0 -> unlimited
    assert len(kept) == 200, "only the model's `drop` removes an item, never a cap"


def test_politics_is_dropped_wherever_the_model_put_it():
    """The prompt says drop politics. On a real day the model instead parked four
    geopolitics threads in `skim`, following its own "when in doubt, skim" rule."""
    items = [
        {"id": 1, "tier": "must_see_push", "one_liner": "某国大选被指遭干预", "scores": {}},
        {
            "id": 2,
            "tier": "skim",
            "one_liner": "两国就制裁互放狠话",
            "why_for_me": "地缘口水战",
            "scores": {},
        },
        {"id": 3, "tier": "must_see_push", "one_liner": "本地 agent 存在 RCE", "scores": {}},
    ]
    kept, push_ids = enforce_caps(items, {**DEFAULT_CAPS, "politics_blocklist": POLITICS})

    assert [i["id"] for i in kept] == [3], "not pushed, and not on the page either"
    assert push_ids == ["3"]
    assert items[0]["dropped_by"] == "politics:大选"
    assert items[1]["dropped_by"] == "politics:地缘"


def test_the_guard_reads_the_models_summary_not_the_raw_post():
    """Scanning raw text would eat real security work that merely mentions politics."""
    item = {
        "id": 1,
        "tier": "recommend",
        "one_liner": "选举系统投票机曝 RCE",  # its own summary IS political-looking
        "content": "unrelated body",
        "scores": {},
    }
    safe = {
        "id": 2,
        "tier": "recommend",
        "one_liner": "勒索团伙部署新加载器",
        "why_for_me": "和你的自托管服务有关",
        "content": "该团伙已被多国制裁，白宫发表声明……",  # raw text is political
        "scores": {},
    }
    kept, _ = enforce_caps([item, safe], {**DEFAULT_CAPS, "politics_blocklist": POLITICS})
    assert [i["id"] for i in kept] == [2], "the summary decides, not the body"


def test_the_politics_blocklist_is_configurable():
    items = [{"id": 1, "tier": "recommend", "one_liner": "crypto rug pull", "scores": {}}]
    kept, _ = enforce_caps(items, {**DEFAULT_CAPS, "politics_blocklist": ["rug pull"]})
    assert kept == []


def test_dropped_politics_is_counted_not_silently_vanished():
    records = [rec("1"), rec("2")]
    llm = {
        "items": [
            {**llm_item("1", "skim"), "one_liner": "白宫就网络战发声"},
            llm_item("2", "recommend"),
        ]
    }
    bundle = build_day_bundle(
        date="2026-07-12",
        run_id=1,
        records=records,
        llm_output=llm,
        caps={"politics_blocklist": POLITICS},
    )

    assert bundle["stats"]["dropped_political"] == 1
    assert len(bundle["items"]) == 1


# ------------------------------------------------------------- build_day_bundle


def test_the_model_cannot_stuff_the_push_by_listing_ids():
    records = [rec(str(i)) for i in range(1, 6)]
    llm = {
        "items": [llm_item(str(i), "skim", relevance=i) for i in range(1, 6)],
        # Every item named as a push, while every item is tagged `skim`.
        "push_item_ids": [f"ext-{i}" for i in range(1, 6)],
    }

    bundle = build_day_bundle(
        date="2026-07-12", run_id=1, records=records, llm_output=llm, base_url="https://m.test"
    )

    assert bundle["push_item_ids"] == [], "the tiers decide, not the list"
    assert bundle["caps"]["delivered_actual"] == 0
    assert len(bundle["items"]) == 5, "still all on the page"


def test_push_ids_are_recomputed_from_tiers_not_copied_from_the_model():
    records = [rec("1"), rec("2")]
    llm = {
        "items": [llm_item("1", "skim"), llm_item("2", "must_see_push")],
        # The model names a skim item as a push. Ignored.
        "push_item_ids": ["ext-1"],
    }
    bundle = build_day_bundle(date="2026-07-12", run_id=1, records=records, llm_output=llm)

    pushed = push_items(bundle)
    assert [p["external_id"] for p in pushed] == ["ext-2"]


def test_invented_items_are_dropped_and_counted():
    records = [rec("1")]
    llm = {"items": [llm_item("1", "recommend"), llm_item("999", "must_see_push")]}

    bundle = build_day_bundle(date="2026-07-12", run_id=1, records=records, llm_output=llm)

    assert len(bundle["items"]) == 1
    assert bundle["items"][0]["external_id"] == "ext-1"
    assert bundle["stats"]["dropped_unmatched"] == 1


def test_unknown_tier_is_normalized_and_drop_is_excluded():
    records = [rec("1"), rec("2")]
    llm = {
        "items": [
            {**llm_item("1", "nonsense_tier")},
            {**llm_item("2", "drop")},
        ]
    }
    bundle = build_day_bundle(date="2026-07-12", run_id=1, records=records, llm_output=llm)

    assert len(bundle["items"]) == 1
    assert bundle["items"][0]["tier"] == "skim"


def test_stats_come_from_the_input_rows_not_the_model():
    records = [rec("1", source="src_a"), rec("2", source="src_b"), rec("3", source="src_b")]
    llm = {"items": [llm_item("1", "recommend", source="src_a")], "push_item_ids": []}

    bundle = build_day_bundle(date="2026-07-12", run_id=1, records=records, llm_output=llm)

    assert bundle["stats"]["ingest_total"] == 3
    assert bundle["stats"]["by_source"] == {"src_a": 1, "src_b": 2}


def test_items_link_to_the_real_stored_url():
    records = [rec("1", url="https://x.com/a/1")]
    llm = {"items": [llm_item("1", "must_see_push")]}
    bundle = build_day_bundle(
        date="2026-07-12", run_id=1, records=records, llm_output=llm, base_url="https://m.test"
    )
    assert bundle["items"][0]["url"] == "https://x.com/a/1"


def test_day_url_is_scoped_to_the_source_and_carries_the_token():
    bundle = build_day_bundle(
        date="2026-07-12",
        run_id=1,
        records=[rec("1")],
        llm_output={"items": [llm_item("1", "recommend")]},
        base_url="https://m.test",
        day_token="secret",
    )
    assert bundle["day_url"] == "https://m.test/day/src_a/2026-07-12?k=secret"
    assert bundle["source_id"] == "src_a"


def test_title_comes_from_the_source_display_name():
    bundle = build_day_bundle(
        date="2026-07-12",
        run_id=1,
        records=[rec("1")],
        llm_output={"items": [llm_item("1", "recommend")]},
        source_names={"src_a": "推特安全流"},
    )
    assert bundle["title"] == "推特安全流"


def test_the_biggest_contributor_is_the_primary_source():
    records = [rec("1", source="src_a"), rec("2", source="src_b"), rec("3", source="src_b")]
    bundle = build_day_bundle(date="2026-07-12", run_id=1, records=records, llm_output={})
    assert bundle["source_id"] == "src_b"


# ---------------------------------------------------------------------- digest


def _bundle(spec: dict, *, day: bool = True) -> dict:
    """spec: {tier: n}."""
    n = 0
    records, llm = [], []
    for tier, count in spec.items():
        for _ in range(count):
            n += 1
            records.append(rec(str(n)))
            llm.append(llm_item(str(n), tier))
    return build_day_bundle(
        date="2026-07-12",
        run_id=1,
        records=records,
        llm_output={"items": llm},
        base_url="https://m.test" if day else "",
        day_token="tok",
        source_names={"src_a": "推特安全流"},
    )


def test_the_push_carries_both_sections():
    text = render_digest(
        _bundle({"must_see_push": 3, "must_see_page": 3, "recommend": 5, "skim": 40})
    )

    assert "🔴 **必看**" in text
    assert "🟡 **推荐**" in text
    assert "速览" not in text, "the long tail belongs on the page"
    assert text.count("[原文 ↗]") == 11, "6 必看 + 5 推荐, each with a way to the post"


def test_no_url_is_ever_printed_raw():
    """A wall of x.com/status/2075… is unreadable, and the day token has no
    business being visible in a group chat."""
    text = render_digest(_bundle({"must_see_push": 2, "recommend": 2}))

    for line in text.splitlines():
        for url in re.findall(r"https?://[^\s)]+", line):
            assert f"]({url})" in line, f"bare URL in the message: {url}"


def test_the_jumps_are_labelled():
    text = render_digest(_bundle({"must_see_push": 1}))

    assert "[原文 ↗](https://example.com/1)" in text
    assert "[📖 查看今日详情 →](https://m.test/day/src_a/2026-07-12?k=tok)" in text


def test_the_digest_is_titled_by_the_source():
    assert render_digest(_bundle({"recommend": 1})).startswith("⚡ 推特安全流 · 2026-07-12")


def test_a_quiet_day_says_so():
    bundle = build_day_bundle(
        date="2026-07-12",
        run_id=1,
        records=[rec("1")],
        llm_output={"items": [llm_item("1", "skim")]},
        base_url="https://m.test",
    )
    text = render_digest(bundle)
    assert "今日无必看条目" in text
    assert "🟡" not in text


def test_a_full_day_fits_the_budget_without_trimming():
    """必看 ≤ 8 plus 推荐 ≤ 15 is the worst case, and it has to fit as it is."""
    bundle = _bundle({"must_see_push": 8, "recommend": 15})
    for item in bundle["items"]:  # every field at its clip limit
        item["one_liner"] = "长" * 80
        item["why_for_me"] = "解" * 70

    text = render_digest(bundle)

    assert len(text) <= MAX_DIGEST_CHARS
    assert "另有" not in text, "a normal day is never trimmed"
    assert text.count("\n- ") == 15


def test_an_oversized_day_trims_whole_recommendations_and_admits_it():
    """Never a mid-sentence cut, and never a silent loss — the page still has them."""
    bundle = _bundle({"must_see_push": 8, "recommend": 15})

    text = render_digest(bundle, max_chars=700)

    assert len(text) <= 700
    assert "另有" in text and "见详情" in text
    assert "[📖 查看今日详情 →]" in text, "the way out is never what gets trimmed"
    assert len(bundle["items"]) == 23, "nothing was removed from the day itself"


def test_must_see_is_never_trimmed_for_length():
    bundle = _bundle({"must_see_push": 8, "recommend": 15})

    text = render_digest(bundle, max_chars=700)

    assert all(f"{n}. **" in text for n in range(1, 9)), "必看 survives even a brutal budget"
