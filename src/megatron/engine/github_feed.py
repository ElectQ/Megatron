"""Aggregate a day of GitHub followee activity for its own page.

The GitHub source is a stream of "X starred Y" / "X forked Y" events. The value
is not any single event — it is the *shape* of the day: which repos several
people you follow converged on, what each person is into, and the raw timeline.

That shape is computed here, deterministically, from the stored `ItemRecord`
rows — never from the LLM bundle. The tiering prompt merges duplicate events for
the same repo and drops all but one, which is exactly the information a
repo-centric view needs (it would lose "these six people starred it"). So the
LLM is used only for annotation ("what is this repo"), joined back on by repo
full name; the counts, stargazer lists and timeline come from the rows.

All the fragile string-parsing lives in `parse_github_event` and nowhere else:
the collector's structured fields (`who`/`action`/`target`/`refs`) are dropped at
the ingest boundary, so repo/action are recovered from `url`/`content`/`tags`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

from ..core.models import ItemRecord

# "alice forked owner/repo → alice/repo" — the repo worth surfacing is the SOURCE
# (left of the arrow), not the fork copy the event URL points at.
_FORK_RE = re.compile(r"forked\s+(?P<src>[^\s]+)\s*(?:→|->)")
_SEG_RE = re.compile(r"^[^/\s]+$")

# Events that name a repo (as opposed to a follow). Sentinel's `is_repo_event`.
# star/fork are the daily bulk; created (a followee starts a new project) and
# release (a followee cuts a version) are rarer but higher-signal, so they get
# their own lane rather than being lumped in as stars.
REPO_ACTIONS = ("star", "fork", "created", "release", "public_repo")
# star + fork drive the convergence ("热门仓库") ranking; created/release do not —
# they are events *about* a repo, not votes *for* it.
HIGHLIGHT_ACTIONS = ("created", "release", "public_repo")


@dataclass
class GhEvent:
    action: str  # star | fork | created | release | public_repo
    repo: str  # "owner/name", or "" if unrecoverable
    repo_url: str
    who: str
    who_url: str
    circle_count: int
    at: datetime | None
    text: str = ""  # the collector's human sentence, e.g. "alice released v2.0"


def _kind_from_tags(tags: list) -> str:
    for t in tags or []:
        s = str(t)
        if s.startswith("kind:"):
            return s.split(":", 1)[1]
    return ""


def _repo_from_url(url: str) -> str:
    """First two path segments as "owner/name", or "" if it is not a repo URL.

    Takes the *prefix* so it also handles deep links — a release URL is
    ``github.com/owner/repo/releases/tag/v2`` and a new-repo URL is
    ``github.com/owner/repo``, both yielding ``owner/repo``. A one-segment path
    (a user profile) is not a repo and returns "".
    """
    segs = [s for s in urlparse(url or "").path.strip("/").split("/") if s]
    if len(segs) < 2 or not (_SEG_RE.match(segs[0]) and _SEG_RE.match(segs[1])):
        return ""
    return f"{segs[0]}/{segs[1]}"


def parse_github_event(record: ItemRecord) -> GhEvent | None:
    """Recover the structured event from a stored row. None if it is not a repo event.

    The only place that knows the collector's text shape — everything downstream
    works on `GhEvent`.
    """
    content = record.content or ""
    action = _kind_from_tags(record.tags)
    if not action:
        if "starred" in content:
            action = "star"
        elif "forked" in content:
            action = "fork"
        elif "created" in content:
            action = "created"
        elif "released" in content:
            action = "release"

    if action == "fork":
        m = _FORK_RE.search(content)
        repo = m.group("src").strip() if m else ""
        if not _repo_from_url(f"https://x/{repo}"):
            repo = ""
    else:
        # star / created / release / public_repo all name their repo in the URL.
        repo = _repo_from_url(record.url)

    if not repo:
        return None  # a follow, a profile star, or something we cannot place

    who = record.author or ""
    who_url = ""
    for link in record.links or []:
        s = str(link)
        if who and s.rstrip("/").lower().endswith(f"github.com/{who.lower()}"):
            who_url = s
            break
    if not who_url and who:
        who_url = f"https://github.com/{who}"

    metrics = record.metrics or {}
    try:
        circle = int(metrics.get("circle_count") or 0)
    except (TypeError, ValueError):
        circle = 0

    return GhEvent(
        action=action,
        repo=repo,
        repo_url=f"https://github.com/{repo}",
        who=who,
        who_url=who_url,
        circle_count=circle,
        at=record.published_at,
        text=content,
    )


def _iso(dt: datetime | None) -> str:
    return dt.isoformat() if dt else ""


def build_annotations(bundle: dict | None) -> dict[str, dict]:
    """Map repo full-name -> {one_liner, topics, tier} from the latest LLM run.

    Optional enrichment: the page renders fine without it (bundle is None on a day
    the analysis has not run yet). Keyed by the same repo rule the aggregation
    uses so the two line up.
    """
    out: dict[str, dict] = {}
    for item in (bundle or {}).get("items") or []:
        repo = _repo_from_url(item.get("url") or item.get("original_url") or "")
        if not repo or repo in out:
            continue
        out[repo] = {
            "one_liner": item.get("one_liner") or "",
            "topics": item.get("topics") or [],
            "tier": item.get("tier") or "",
        }
    return out


# Tiers the LLM marks as "worth surfacing"; used only to float valuable
# single-star repos above the rest, never to hide anything.
_VALUED_TIERS = {"must_see_push", "must_see_page", "recommend"}


def aggregate_github_day(records: list[ItemRecord], annotations: dict | None = None) -> dict:
    """The whole page context: repos (convergence-ranked), by-who, timeline, stats."""
    annotations = annotations or {}
    events = [e for e in (parse_github_event(r) for r in records) if e is not None]

    repos: dict[str, dict] = {}
    who: dict[str, dict] = {}
    timeline: list[dict] = []
    highlights: list[dict] = []
    counts = {"star": 0, "fork": 0, "created": 0, "release": 0}

    for e in events:
        counts[e.action] = counts.get(e.action, 0) + 1

        # star/fork build the convergence buckets; created/release are events
        # *about* a repo, not votes for it, so they go to their own lane.
        if e.action in ("star", "fork"):
            bucket = repos.setdefault(
                e.repo,
                {
                    "repo": e.repo,
                    "repo_url": e.repo_url,
                    "stargazers": [],
                    "forkers": [],
                    "last_at": None,
                    "ann": annotations.get(e.repo) or {},
                },
            )
            seen = {p["login"] for p in bucket["stargazers"] + bucket["forkers"]}
            if e.who and e.who not in seen:
                person = {"login": e.who, "url": e.who_url}
                (bucket["forkers"] if e.action == "fork" else bucket["stargazers"]).append(person)
            if e.at and (bucket["last_at"] is None or e.at > bucket["last_at"]):
                bucket["last_at"] = e.at
        elif e.action in HIGHLIGHT_ACTIONS:
            highlights.append(
                {
                    "action": e.action,
                    "repo": e.repo,
                    "repo_url": e.repo_url,
                    "who": e.who,
                    "who_url": e.who_url,
                    "text": e.text,
                    "at": _iso(e.at),
                    "ann": annotations.get(e.repo) or {},
                }
            )

        actor = who.setdefault(e.who, {"login": e.who, "url": e.who_url, "touched": []})
        actor["touched"].append(
            {"repo": e.repo, "repo_url": e.repo_url, "action": e.action, "at": _iso(e.at)}
        )
        timeline.append(
            {
                "at": _iso(e.at),
                "who": e.who,
                "who_url": e.who_url,
                "action": e.action,
                "repo": e.repo,
                "repo_url": e.repo_url,
            }
        )

    repo_list = []
    for r in repos.values():
        r["count"] = len(r["stargazers"])
        r["last_at_iso"] = _iso(r["last_at"])
        r.pop("last_at", None)
        repo_list.append(r)

    # Star-most first — that is the headline signal. Ties broken by most recent.
    repo_list.sort(key=lambda r: (r["count"], r["last_at_iso"]), reverse=True)

    trending = [r for r in repo_list if r["count"] >= 2]
    singles = [r for r in repo_list if r["count"] < 2]
    # Among single-star repos, let an LLM "worth it" verdict float up, else recency.
    singles.sort(
        key=lambda r: (r["ann"].get("tier") in _VALUED_TIERS, r["last_at_iso"]), reverse=True
    )

    highlights.sort(key=lambda h: h["at"], reverse=True)

    by_who = sorted(who.values(), key=lambda w: len(w["touched"]), reverse=True)
    for w in by_who:
        w["count"] = len(w["touched"])

    timeline.sort(key=lambda e: e["at"], reverse=True)

    return {
        "highlights": highlights,
        "trending": trending,
        "singles": singles,
        "by_who": by_who,
        "timeline": timeline,
        "stats": {
            "total": len(events),
            "repo_count": len(repo_list),
            "actor_count": len(by_who),
            "star_count": counts["star"],
            "fork_count": counts["fork"],
            "created_count": counts["created"],
            "release_count": counts["release"],
        },
    }


__all__ = [
    "GhEvent",
    "HIGHLIGHT_ACTIONS",
    "REPO_ACTIONS",
    "aggregate_github_day",
    "build_annotations",
    "parse_github_event",
]
