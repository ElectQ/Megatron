"""The public projection of a GitHub follow-feed day: repos, never people.

The private page (`day_github.html`) answers "what is my circle doing" — its
three columns are a timeline of who acted when, a list of who touched what, and
repo cards with the stargazers' faces on them. Every one of those is the follow
graph drawn from a different angle, and the follow graph is the thing this stream
must not leak (see `public_view`'s module docstring).

So the public page is not that page with names blanked out — it is a different
page built from the same day. Its sections are the model's tiers — must-see,
recommended, then the rest — so the reader gets the day's judgement, not just a
list. The one signal that survives redaction — *how many independent accounts
converged on a repo today* — is still what ranks the board internally, but it is
no longer printed: a ×N counter reads as "N of my friends starred this", which
discloses the circle's size. Repo-own facts (the repo's own star count, from the
enrichment fetch when available) are fine and shown instead.


Two things are deliberately *not* in `stats`, though the private page shows both:
`actor_count` and `star_count`. Neither names anyone, but both measure the
circle — how many people the reader follows, and how busy they were. That is a
property of the reader, not of the day's repos, so it stays home.

`newcomers` is the exception that proves the rule, and it is safe by construction
rather than by redaction: `FollowEvent` carries the *target* of a follow and
never the actor (see `engine.github_feed`), so what surfaces is a public
account's own public profile. Publishing it still discloses, in aggregate, that
*someone* started watching them — a curation signal, not an identity — so it is
opt-out per source via `config.public_newcomers`.
"""

from __future__ import annotations

from ..engine.github_feed import _repo_from_url, aggregate_github_day
from .public_view import EMPTY, Policy, is_public

# How many topics the rail shows. Beyond this it stops being a summary of the day.
_TOPIC_LIMIT = 12


def held_back_repos(bundle: dict | None, policy: Policy = EMPTY) -> set[str]:
    """Repos the model or the operator decided not to publish.

    The public repo cards are built from the stored rows, not from the bundle's
    items — so an item-level publish decision would otherwise have nothing to act
    on. Mapping the held-back items back to their repo and dropping the repo
    entirely is what makes "hide this one" mean the same thing here as it does on
    a tweet: the reader sees no trace of it.
    """
    source_id = (bundle or {}).get("source_id", "")
    date = (bundle or {}).get("date", "")
    out: set[str] = set()
    for item in (bundle or {}).get("items") or []:
        if is_public(item, source_id, date, policy):
            continue
        repo = _repo_from_url(item.get("url") or item.get("original_url") or "")
        if repo:
            out.add(repo)
    return out


def _repo_card(r: dict) -> dict:
    """One aggregated repo, stripped to what a stranger may see.

    `count` ranks the board but is never copied into the public card: a
    "×4 starred today" numeral is the circle's activity in disguise. The
    repo-own `stars` (from the enrichment fetch, when the day had one) is a
    fact about the repo, not about who follows it, and is the only number kept.
    """
    ann = r.get("ann") or {}
    return {
        "repo": r["repo"],
        "repo_url": r["repo_url"],
        "one_liner": ann.get("one_liner") or "",
        "topics": ann.get("topics") or [],
        "stars": ann.get("repo_stars"),
    }


def _highlight(h: dict) -> dict:
    """A created/released event, minus the person who did it.

    `who`, `who_url` and `text` all name the actor — `text` is the collector's
    raw sentence ("alice released v2.0"), so it is dropped rather than shown.
    `ref` (the tag) is the one thing worth keeping from it, and it is parsed from
    the URL, not from the sentence.
    """
    ann = h.get("ann") or {}
    return {
        "action": h["action"],
        "repo": h["repo"],
        "repo_url": h["repo_url"],
        "ref": h.get("ref") or "",
        "one_liner": ann.get("one_liner") or "",
        "topics": ann.get("topics") or [],
    }


def _topics(cards: list[dict], highlights: list[dict]) -> list[dict]:
    """The day's topics with their frequency, most common first."""
    counts: dict[str, int] = {}
    for entry in [*cards, *highlights]:
        for topic in entry.get("topics") or []:
            counts[topic] = counts.get(topic, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    return [{"name": name, "count": n} for name, n in ranked[:_TOPIC_LIMIT]]


def public_github_view(
    records: list,
    bundle: dict | None,
    source_id: str,
    date: str,
    title: str,
    policy: Policy = EMPTY,
    show_newcomers: bool = True,
) -> dict:
    """The whole public page context for one day of a GitHub feed.

    `records` are the day's stored rows and `bundle` the latest analysis of them —
    the same two inputs the private page uses, so the two pages cannot drift into
    disagreeing about what happened. The analysis is optional: without it the
    repos still rank and the page still renders, just without the one-liners.
    """
    from ..engine.github_feed import build_annotations

    # The hard gate, restated here rather than trusted to the caller. `held_back_
    # repos` cannot stand in for it: it can only hide repos the *analysis* saw, so
    # a repo the model never annotated would sail straight through onto a page
    # that should not exist. A personal source publishes nothing, full stop.
    if not policy.source_public(source_id):
        records = []
        bundle = None

    agg = aggregate_github_day(records, build_annotations(bundle))
    hidden = held_back_repos(bundle, policy)

    cards = [_repo_card(r) for r in [*agg["trending"], *agg["singles"]] if r["repo"] not in hidden]
    # Ranking still uses the internal convergence order (agg sorts by it) — just
    # never printed. The sections are the model's verdict, like the push:
    # 必看 / 推荐 / the rest.
    tiers = {
        r["repo"]: (r.get("ann") or {}).get("tier") or ""
        for r in [*agg["trending"], *agg["singles"]]
    }
    must_see = [c for c in cards if tiers.get(c["repo"]) in ("must_see_push", "must_see_page")]
    recommended = [c for c in cards if tiers.get(c["repo"]) == "recommend"]
    tiered = {c["repo"] for c in must_see} | {c["repo"] for c in recommended}
    others = [c for c in cards if c["repo"] not in tiered]

    lead = must_see[0] if must_see else None

    highlights = [_highlight(h) for h in agg["highlights"] if h["repo"] not in hidden]
    newcomers = agg["newcomers"] if show_newcomers else []

    return {
        "source_id": source_id,
        "date": date,
        "title": title,
        "lead": lead,
        "must_see": must_see,
        "recommended": recommended,
        "others": others,
        "highlights": highlights,
        "newcomers": newcomers,
        "topics": _topics(cards, highlights),
        "stats": {
            "repo_count": len(cards),
            "must_see_count": len(must_see),
            "recommend_count": len(recommended),
            "created_count": sum(1 for h in highlights if h["action"] != "release"),
            "release_count": sum(1 for h in highlights if h["action"] == "release"),
            "newcomer_count": len(newcomers),
        },
    }


def has_public_github(view: dict) -> bool:
    """Is there anything on this page at all?"""
    return bool(
        view["lead"] or view["others"] or view["highlights"] or view["newcomers"] or view["recommended"]
    )


__all__ = ["has_public_github", "held_back_repos", "public_github_view"]
