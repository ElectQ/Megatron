"""The day bundle: one run's full, tiered view of a day (§3.3).

Two jobs, both of which exist because a prompt cannot be trusted to enforce its
own limits:

* `enforce_caps` re-derives `push_item_ids` from the tiers the model assigned,
  after capping them. Whatever the model *claims* it wants to push is only a
  ranking hint. A model that decides eight things are urgent does not get to
  send eight notifications.
* `build_day_bundle` resolves every model-returned item back to a real row via
  `(source_id, external_id)`. An item that does not resolve was invented, and is
  dropped and counted rather than rendered.

Stats come from the input rows, never from the model.
"""

from __future__ import annotations

from typing import Any

from ..core.logging import get_logger
from ..core.models import ItemRecord

logger = get_logger(__name__)

BUNDLE_SCHEMA = "day_bundle_v1"

TIERS = ("must_see_push", "must_see_page", "recommend", "skim", "drop")

# Overflow is demoted one rung, not discarded: the model still judged it worth
# reading, it just did not make the cut for an interruption.
DEMOTE = {
    "must_see_push": "must_see_page",
    "must_see_page": "recommend",
    "recommend": "skim",
    "skim": "drop",
}

DEFAULT_CAPS = {
    "must_see_push_max": 3,
    "must_see_page_max": 6,
    "recommend_max": 10,
    "skim_max": 10,
}

_CAP_KEY = {
    "must_see_push": "must_see_push_max",
    "must_see_page": "must_see_page_max",
    "recommend": "recommend_max",
    "skim": "skim_max",
}


def item_link(rec: ItemRecord, base_url: str = "") -> str:
    """The URL a reader is sent to for this item.

    Single swap point: pointing this at a tracked redirect later changes every
    rendered link at once.
    """
    return rec.url


def day_url(base_url: str, date: str, token: str = "") -> str:
    url = f"{base_url.rstrip('/')}/day/{date}"
    return f"{url}?k={token}" if token else url


def _rank(item: dict) -> tuple:
    """Order within a tier: the model's scores, then its own stated ordering."""
    scores = item.get("scores") or {}

    def num(key: str) -> float:
        try:
            return float(scores.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    return (
        num("relevance"),
        num("actionability"),
        num("confidence"),
        -int(item.get("_push_rank", 9999)),
    )


def enforce_caps(items: list[dict], caps: dict) -> tuple[list[dict], list[str]]:
    """Cap each tier, demoting the overflow. Returns (items, push_item_ids).

    `push_item_ids` is *computed*, never copied from the model's answer.
    """
    caps = {**DEFAULT_CAPS, **(caps or {})}

    # Highest tier first, so anything demoted lands in a tier we have yet to cap
    # and gets capped again on the next iteration.
    for tier in ("must_see_push", "must_see_page", "recommend", "skim"):
        limit = int(caps.get(_CAP_KEY[tier], DEFAULT_CAPS[_CAP_KEY[tier]]))
        in_tier = [i for i in items if i.get("tier") == tier]
        if len(in_tier) <= limit:
            continue
        in_tier.sort(key=_rank, reverse=True)
        for loser in in_tier[limit:]:
            loser["tier"] = DEMOTE[tier]
            loser.setdefault("demoted_from", tier)

    kept = [i for i in items if i.get("tier") != "drop"]

    push = [i for i in kept if i.get("tier") == "must_see_push"]
    push.sort(key=_rank, reverse=True)
    push_item_ids = [str(i["id"]) for i in push]

    return kept, push_item_ids


def build_day_bundle(
    *,
    date: str,
    run_id: int,
    records: list[ItemRecord],
    llm_output: dict,
    caps: dict | None = None,
    intent: dict | None = None,
    base_url: str = "",
    day_token: str = "",
    timezone: str = "Asia/Shanghai",
    warnings: list[dict] | None = None,
) -> dict:
    caps = {**DEFAULT_CAPS, **(caps or {})}
    by_key: dict[tuple[str, str], ItemRecord] = {(r.source, r.item_id): r for r in records}

    items: list[dict] = []
    unmatched = 0

    for rank, raw in enumerate(llm_output.get("items") or []):
        external_id = str(raw.get("external_id") or "").strip()
        source_id = str(raw.get("source_id") or "").strip()

        rec = by_key.get((source_id, external_id))
        if rec is None and external_id:
            # Be forgiving about a wrong/missing source_id when the id is unique.
            matches = [r for k, r in by_key.items() if k[1] == external_id]
            rec = matches[0] if len(matches) == 1 else None

        if rec is None:
            unmatched += 1
            continue

        tier = str(raw.get("tier") or "").strip()
        if tier not in TIERS:
            tier = "skim"
        if tier == "drop":
            continue

        items.append(
            {
                "id": rec.id,
                "source_id": rec.source,
                "external_id": rec.item_id,
                "tier": tier,
                "one_liner": str(raw.get("one_liner") or "").strip(),
                "why_for_me": str(raw.get("why_for_me") or "").strip(),
                "bullets": [str(b) for b in (raw.get("bullets") or [])],
                "actionability": str(raw.get("actionability") or "read"),
                "scores": raw.get("scores") or {},
                "topics": [str(t) for t in (raw.get("topics") or [])],
                "author": rec.author,
                "author_name": rec.author_name,
                "published_at": rec.published_at.isoformat() if rec.published_at else "",
                "url": item_link(rec, base_url),
                "original_url": rec.url,
                "content": rec.content,
                "metrics": rec.metrics or {},
                "_push_rank": rank,
            }
        )

    if unmatched:
        logger.warning("bundle.unmatched_items", run_id=run_id, count=unmatched)

    items, push_item_ids = enforce_caps(items, caps)
    for i in items:
        i.pop("_push_rank", None)

    by_source: dict[str, int] = {}
    for r in records:
        by_source[r.source] = by_source.get(r.source, 0) + 1

    tier_counts: dict[str, int] = {}
    for i in items:
        tier_counts[i["tier"]] = tier_counts.get(i["tier"], 0) + 1

    return {
        "schema": BUNDLE_SCHEMA,
        "bundle_id": f"day-{date}",
        "date": date,
        "timezone": timezone,
        "run_id": run_id,
        "intent": intent or {},
        "caps": {**caps, "must_see_push_actual": len(push_item_ids)},
        "stats": {
            "ingest_total": len(records),
            "by_source": by_source,
            "by_tier": tier_counts,
            "dropped_unmatched": unmatched,
        },
        "items": items,
        "push_item_ids": push_item_ids,
        "day_url": day_url(base_url, date, day_token) if base_url else "",
        "warnings": warnings or [],
    }


def push_items(bundle: dict) -> list[dict]:
    ids = set(bundle.get("push_item_ids") or [])
    return [i for i in bundle.get("items") or [] if str(i.get("id")) in ids]


def sections(bundle: dict) -> dict[str, list[dict]]:
    """Group the bundle's items by source, for the day page."""
    out: dict[str, list[dict]] = {}
    for item in bundle.get("items") or []:
        out.setdefault(item["source_id"], []).append(item)
    return out


__all__: list[Any] = [
    "BUNDLE_SCHEMA",
    "DEFAULT_CAPS",
    "TIERS",
    "build_day_bundle",
    "day_url",
    "enforce_caps",
    "item_link",
    "push_items",
    "sections",
]
