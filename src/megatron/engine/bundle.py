"""The day bundle: one run's full, tiered view of a day (§3.3).

Two jobs, both of which exist because a prompt cannot be trusted to enforce its
own limits:

* `enforce_caps` re-derives `push_item_ids` from the tiers the model assigned,
  after fitting them to the caps. Whatever the model *claims* it wants to push is
  only a ranking hint.
* `build_day_bundle` resolves every model-returned item back to a real row via
  `(source_id, external_id)`. An item that does not resolve was invented, and is
  dropped and counted rather than rendered.

Stats come from the input rows, never from the model.

The push carries 必看 + 推荐; the page carries everything (速览 included). So the
caps are floors as much as ceilings: the risk is not "the model interrupts you
twenty times", it is "the model hands back a thin day and the message reads as if
nothing ran".
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

# Every cap here has a floor under it, because the failure that actually happens
# is a thin day, not a spammy one: a stingy model hands back two items and the
# push reads as "nothing ran" rather than "nothing was urgent".
#
# `lead_min` is a *minimum*, not a maximum — 至少三条打头，不是最多三条. There is no
# separate ceiling on the lead: `must_see_max` bounds 必看 as a whole, and the lead
# is a subset of it.
#
# `skim_max: 0` = unlimited. An item disappears from the page because the model
# judged it worthless (`drop`), never because it fell off the end of a cap.
#
# NOTE: these are a NEUTRAL fallback, not the product's real thresholds. The
# operative caps come from `config/policy.yaml` (loaded by the runner) and can be
# overridden per-task via `filter_config.caps`. A permissive fallback keeps the
# engine running if no policy file is present, without baking product policy back
# into the framework. All-zero == "no floor, no ceiling, nothing trimmed".
DEFAULT_CAPS = {
    "lead_min": 0,
    "must_see_min": 0,
    "must_see_max": 0,
    "recommend_max": 0,
    "skim_max": 0,
}

MUST_SEE = ("must_see_push", "must_see_page")

# What the webhook carries. 速览 is page-only — it is the long tail, and putting it
# in a chat message is what the day page exists to avoid. (Engine mechanic, not a
# tunable filter value — stays in code.)
DELIVERED = ("must_see_push", "must_see_page", "recommend")

# The political-topic blocklist is product policy → it lives in config/policy.yaml,
# not here. This empty fallback means "no politics filtering" when no policy /
# per-task blocklist is supplied. `_political` matches against the model's OWN
# words (one_liner / why_for_me / topics), never the raw post, so a CVE writeup
# that merely mentions an election is not swept up.
POLITICS: tuple[str, ...] = ()


def _political(item: dict, blocklist: tuple[str, ...]) -> str:
    """The word that makes this political, or "" — judged on the model's summary."""
    haystack = " ".join(
        [
            item.get("one_liner") or "",
            item.get("why_for_me") or "",
            " ".join(item.get("topics") or []),
        ]
    ).lower()
    for word in blocklist:
        if word.lower() in haystack:
            return word
    return ""


def item_link(rec: ItemRecord, base_url: str = "") -> str:
    """The URL a reader is sent to for this item.

    Single swap point: pointing this at a tracked redirect later changes every
    rendered link at once.
    """
    return rec.url


def day_url(base_url: str, date: str, source_id: str = "", token: str = "") -> str:
    """The digest lives under its source:每个源一份日刊，各自渲染。"""
    path = f"/day/{source_id}/{date}" if source_id else f"/day/{date}"
    url = f"{base_url.rstrip('/')}{path}"
    return f"{url}?k={token}" if token else url


def public_url(base_url: str, date: str, source_id: str = "", lang: str = "zh") -> str:
    """World-readable blog page for a day's public digest — no capability token.

    Distinct from `day_url`: language-prefixed, indexable, safe to drop in a group
    chat. It only resolves to a real page when the day has `public: true` items
    (the public route 404s by design otherwise), so callers hand it out only when
    the bundle actually published something — else the push falls back to day_url.
    """
    if not base_url or not source_id:
        return ""
    return f"{base_url.rstrip('/')}/{lang}/{source_id}/{date}"


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


def _in(items: list[dict], *tiers: str) -> list[dict]:
    return [i for i in items if i.get("tier") in tiers]


def enforce_caps(items: list[dict], caps: dict) -> tuple[list[dict], list[str]]:
    """Fit the model's tiers to the caps. Returns (items, push_item_ids).

    `push_item_ids` is *computed* from the surviving tiers, never copied from the
    model's answer — a model does not get to change what is delivered by listing
    ids. Here it is the delivered set: 必看 + 推荐.
    """
    caps = {**DEFAULT_CAPS, **(caps or {})}
    lead_min = int(caps["lead_min"])
    must_min = int(caps["must_see_min"])
    must_max = int(caps["must_see_max"])
    rec_max = int(caps["recommend_max"])
    skim_max = int(caps["skim_max"])

    def demote(item: dict) -> None:
        item.setdefault("demoted_from", item["tier"])
        item["tier"] = DEMOTE[item["tier"]]

    def promote(item: dict, to: str) -> None:
        item.setdefault("promoted_from", item["tier"])
        item["tier"] = to

    def best(*tiers: str) -> list[dict]:
        return sorted(_in(items, *tiers), key=_rank, reverse=True)

    # 0. Politics is out of the digest entirely — the model is asked to drop it,
    #    and this is what happens when it doesn't. Runs before the caps so a
    #    dropped item cannot occupy a must-see slot that a real story wanted.
    blocklist = tuple(caps.get("politics_blocklist") or POLITICS)
    for item in _in(items, *MUST_SEE, "recommend", "skim"):
        hit = _political(item, blocklist)
        if hit:
            item["dropped_by"] = f"politics:{hit}"
            item["tier"] = "drop"
            logger.info("bundle.politics_dropped", item_id=item.get("id"), matched=hit)

    # 1. Ceiling on 必看 as a whole. The lead has no separate ceiling — it lives
    #    inside this one.
    if must_max > 0:
        for loser in best(*MUST_SEE)[must_max:]:
            demote(loser)

    # 2. Floor on 必看. A near-empty first screen reads as "nothing ran", not as
    #    "nothing was urgent", so top it up with the best of `recommend`.
    #    Deliberately not from `skim`: skim means "a glance is enough", and
    #    calling that 必看 would be a lie. A genuinely short day stays short.
    shortfall = must_min - len(_in(items, *MUST_SEE))
    for winner in best("recommend")[: max(shortfall, 0)]:
        promote(winner, "must_see_page")

    # 3. Floor on the lead — 最少三条打头，不是最多三条. Without it a cautious model
    #    hands back zero `must_see_push` and the message opens with no headline.
    lead_short = lead_min - len(_in(items, "must_see_push"))
    for winner in best("must_see_page")[: max(lead_short, 0)]:
        promote(winner, "must_see_push")

    # 4. 推荐 is delivered too, so it is bounded; 速览 is page-only and is not.
    if rec_max > 0:
        for loser in best("recommend")[rec_max:]:
            demote(loser)  # -> skim
    if skim_max > 0:
        for loser in best("skim")[skim_max:]:
            demote(loser)  # -> drop

    kept = [i for i in items if i.get("tier") != "drop"]

    delivered = [i for tier in DELIVERED for i in sorted(_in(kept, tier), key=_rank, reverse=True)]
    push_item_ids = [str(i["id"]) for i in delivered]

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
    source_names: dict[str, str] | None = None,
    title: str = "",
    publishable: bool = False,
) -> dict:
    caps = {**DEFAULT_CAPS, **(caps or {})}
    source_names = source_names or {}
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
                "actionability": str(raw.get("actionability") or "read"),
                "scores": raw.get("scores") or {},
                # Tags come from the model. Capped here so a chatty answer cannot
                # turn one card into a wall of chips.
                "topics": [str(t).strip() for t in (raw.get("topics") or []) if str(t).strip()][:4],
                # May this item appear on the public blog? Deliberately tri-state:
                # True/False are the model's explicit calls, None means it did not
                # say. What None *means* is not the model's business — it is the
                # source's policy (a public stream defaults to publishing, a
                # personal one never publishes at all). See web/public_view.
                "public": raw.get("public") if isinstance(raw.get("public"), bool) else None,
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

    # enforce_caps mutates the dicts in place and returns the survivors, so the
    # original list still holds what it threw away.
    scored = items
    items, push_item_ids = enforce_caps(items, caps)
    for i in items:
        i.pop("_push_rank", None)

    # A silent filter is indistinguishable from a bug. Count what the guard ate.
    dropped_political = sum(
        1 for i in scored if str(i.get("dropped_by", "")).startswith("politics:")
    )
    if dropped_political:
        logger.info("bundle.politics_filtered", run_id=run_id, count=dropped_political)

    by_source: dict[str, int] = {}
    for r in records:
        by_source[r.source] = by_source.get(r.source, 0) + 1

    tier_counts: dict[str, int] = {}
    for i in items:
        tier_counts[i["tier"]] = tier_counts.get(i["tier"], 0) + 1

    # The digest is addressed by its source. With several, the primary is simply
    # the one that contributed most — the others still render on their own pages.
    primary = max(by_source, key=lambda s: by_source[s]) if by_source else ""
    primary_name = source_names.get(primary, primary)

    return {
        "schema": BUNDLE_SCHEMA,
        "bundle_id": f"day-{primary}-{date}" if primary else f"day-{date}",
        "date": date,
        "timezone": timezone,
        "run_id": run_id,
        "source_id": primary,
        "source_names": source_names,
        "title": title or primary_name or "情报日刊",
        "intent": intent or {},
        "caps": {**caps, "delivered_actual": len(push_item_ids)},
        "stats": {
            "ingest_total": len(records),
            "by_source": by_source,
            "by_tier": tier_counts,
            "dropped_unmatched": unmatched,
            "dropped_political": dropped_political,
        },
        "items": items,
        "push_item_ids": push_item_ids,
        "day_url": day_url(base_url, date, primary, day_token) if base_url else "",
        # The public blog link, given only when the day will actually have a
        # public page — otherwise "" so a push template falls back to the token
        # day_url instead of pointing at a page that 404s.
        #
        # Two conditions, and the source's is the hard one: a `personal` source
        # never has a public page at all (so its push always links to the token
        # page), and within a publishable source a day needs something that
        # survives the item gate — where absent (None) means publish, and only an
        # explicit False from the model holds an item back.
        "public_url": (
            public_url(base_url, date, primary)
            if base_url
            and publishable
            and any(i.get("public") is not False for i in items)
            else ""
        ),
        "warnings": warnings or [],
    }


def push_items(bundle: dict) -> list[dict]:
    """What goes out over the webhook, in delivery order (必看 then 推荐)."""
    by_id = {str(i.get("id")): i for i in bundle.get("items") or []}
    return [by_id[i] for i in (bundle.get("push_item_ids") or []) if i in by_id]


def push_sections(bundle: dict) -> tuple[list[dict], list[dict]]:
    """The push, split the way it is rendered: (必看, 推荐).

    必看 keeps the lead first — those are the ones the model called urgent — but
    both must-see tiers are one section to the reader.
    """
    delivered = push_items(bundle)
    must_see = [i for i in delivered if i.get("tier") in MUST_SEE]
    recommend = [i for i in delivered if i.get("tier") == "recommend"]
    return must_see, recommend


def sections(bundle: dict) -> dict[str, list[dict]]:
    """Group the bundle's items by source, for the day page."""
    out: dict[str, list[dict]] = {}
    for item in bundle.get("items") or []:
        out.setdefault(item["source_id"], []).append(item)
    return out


__all__: list[Any] = [
    "BUNDLE_SCHEMA",
    "DEFAULT_CAPS",
    "DELIVERED",
    "MUST_SEE",
    "POLITICS",
    "TIERS",
    "build_day_bundle",
    "day_url",
    "public_url",
    "enforce_caps",
    "item_link",
    "push_items",
    "push_sections",
    "sections",
]
