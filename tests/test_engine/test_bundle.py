"""Caps are enforced by code, not by asking the model nicely."""

from __future__ import annotations

from datetime import datetime, timezone

from megatron.core.models import ItemRecord
from megatron.engine.bundle import (
    DEFAULT_CAPS,
    build_day_bundle,
    enforce_caps,
    push_items,
)
from megatron.engine.doorbell import MAX_DOORBELL_CHARS, render_doorbell


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


def test_push_overflow_is_capped_to_three():
    items = [{"id": i, "tier": "must_see_push", "scores": {"relevance": i}} for i in range(8)]
    kept, push_ids = enforce_caps(items, DEFAULT_CAPS)
    assert len(push_ids) == 3


def test_overflow_is_demoted_not_discarded():
    items = [{"id": i, "tier": "must_see_push", "scores": {"relevance": i}} for i in range(5)]
    kept, push_ids = enforce_caps(items, DEFAULT_CAPS)

    assert len(kept) == 5, "nothing is thrown away — it just stops interrupting"
    demoted = [i for i in kept if i["tier"] == "must_see_page"]
    assert len(demoted) == 2
    assert all(i.get("demoted_from") == "must_see_push" for i in demoted)


def test_highest_scoring_items_keep_the_push_slots():
    items = [{"id": i, "tier": "must_see_push", "scores": {"relevance": i}} for i in range(5)]
    _, push_ids = enforce_caps(items, DEFAULT_CAPS)
    assert push_ids == ["4", "3", "2"]


def test_demotion_cascades_through_the_tiers():
    caps = {"must_see_push_max": 1, "must_see_page_max": 1, "recommend_max": 1, "skim_max": 1}
    items = [{"id": i, "tier": "must_see_push", "scores": {"relevance": i}} for i in range(5)]
    kept, push_ids = enforce_caps(items, caps)

    tiers = sorted(i["tier"] for i in kept)
    assert push_ids == ["4"]
    # 5 items, one per tier, and the last falls off the bottom into drop.
    assert tiers == ["must_see_page", "must_see_push", "recommend", "skim"]


def test_zero_push_is_a_valid_day():
    items = [{"id": 1, "tier": "recommend", "scores": {}}]
    _, push_ids = enforce_caps(items, DEFAULT_CAPS)
    assert push_ids == []


# ------------------------------------------------------------- build_day_bundle


def test_model_cannot_push_more_than_the_cap_by_claiming_it():
    records = [rec(str(i)) for i in range(1, 6)]
    llm = {
        "items": [llm_item(str(i), "must_see_push", relevance=i) for i in range(1, 6)],
        # The model asks for five. It does not get five.
        "push_item_ids": [f"ext-{i}" for i in range(1, 6)],
    }

    bundle = build_day_bundle(
        date="2026-07-12", run_id=1, records=records, llm_output=llm, base_url="https://m.test"
    )

    assert len(bundle["push_item_ids"]) == 3
    assert bundle["caps"]["must_see_push_actual"] == 3
    assert len(push_items(bundle)) == 3


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
    assert bundle["stats"]["dropped_unmatched"] == 1
    assert bundle["push_item_ids"] == []


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


def test_day_url_carries_the_capability_token():
    bundle = build_day_bundle(
        date="2026-07-12",
        run_id=1,
        records=[],
        llm_output={},
        base_url="https://m.test",
        day_token="secret",
    )
    assert bundle["day_url"] == "https://m.test/day/2026-07-12?k=secret"


# --------------------------------------------------------------------- doorbell


def _bundle_with(n_push: int) -> dict:
    records = [rec(str(i)) for i in range(1, n_push + 1)]
    llm = {"items": [llm_item(str(i), "must_see_push") for i in range(1, n_push + 1)]}
    return build_day_bundle(
        date="2026-07-12",
        run_id=1,
        records=records,
        llm_output=llm,
        base_url="https://m.test",
        day_token="tok",
    )


def test_doorbell_stays_thin():
    text = render_doorbell(_bundle_with(3))
    assert len(text) <= MAX_DOORBELL_CHARS
    assert text.count("🔴") == 3


def test_doorbell_links_to_the_day_page():
    text = render_doorbell(_bundle_with(2))
    assert "https://m.test/day/2026-07-12?k=tok" in text


def test_doorbell_says_so_when_nothing_is_urgent():
    records = [rec("1")]
    bundle = build_day_bundle(
        date="2026-07-12",
        run_id=1,
        records=records,
        llm_output={"items": [llm_item("1", "recommend")]},
        base_url="https://m.test",
    )
    text = render_doorbell(bundle)
    assert "今日无必看条目" in text
    assert "🔴" not in text


def test_doorbell_never_dumps_the_whole_day():
    """Ten urgent-looking items must still produce a three-item doorbell."""
    records = [rec(str(i)) for i in range(1, 11)]
    llm = {"items": [llm_item(str(i), "must_see_push") for i in range(1, 11)]}
    bundle = build_day_bundle(
        date="2026-07-12", run_id=1, records=records, llm_output=llm, base_url="https://m.test"
    )
    text = render_doorbell(bundle)

    assert text.count("🔴") == 3
    assert len(text) <= MAX_DOORBELL_CHARS
    assert len(bundle["items"]) == 10, "the other seven are still on the day page"
