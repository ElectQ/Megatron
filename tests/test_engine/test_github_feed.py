"""The GitHub page is deterministic aggregation, so it is unit-testable end to end.

The collector's structured fields are dropped at ingest, so repo/action are
recovered by string-parsing url/content/tags. That parsing is the one fragile
spot; these tests pin its behaviour on the real shapes (star and fork) and on
the failure modes (missing tag, a profile URL that is not a repo).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from megatron.core.models import ItemRecord
from megatron.engine.github_feed import (
    aggregate_github_day,
    build_annotations,
    parse_github_event,
)

T0 = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)


def ev(
    who: str,
    action: str,
    repo: str,
    *,
    circle: int = 1,
    minutes: int = 0,
    url: str | None = None,
    content: str | None = None,
    tags: list | None = None,
) -> ItemRecord:
    if action == "fork":
        forkee = f"{who}/{repo.split('/')[-1]}"
        url = url if url is not None else f"https://github.com/{forkee}"
        content = content if content is not None else f"{who} forked {repo} → {forkee}"
        tags = tags if tags is not None else ["kind:fork"]
    elif action == "created":
        url = url if url is not None else f"https://github.com/{repo}"
        content = content if content is not None else f"{who} created repository {repo}"
        tags = tags if tags is not None else ["kind:created"]
    elif action == "release":
        # A release URL is a deep link under the repo.
        url = url if url is not None else f"https://github.com/{repo}/releases/tag/v1.0"
        content = content if content is not None else f"{who} released v1.0 of {repo}"
        tags = tags if tags is not None else ["kind:release"]
    else:  # star
        url = url if url is not None else f"https://github.com/{repo}"
        content = content if content is not None else f"{who} starred {repo}"
        tags = tags if tags is not None else ["kind:star"]
    return ItemRecord(
        item_id=f"event:{who}:{repo}",
        source="github_followee_feed",
        content=content,
        url=url,
        author=who,
        author_name=who,
        published_at=T0 + timedelta(minutes=minutes),
        collected_at=T0,
        collect_date="2026-07-09",
        tags=tags,
        links=[f"https://github.com/{who}"],
        metrics={"circle_count": circle},
    )


# ------------------------------------------------------------------ parsing


def test_a_star_event_yields_the_repo_from_its_url():
    e = parse_github_event(ev("RalfHacker", "star", "MWR-CyberSec/VeeamDumper-BOF", circle=3))
    assert e.action == "star"
    assert e.repo == "MWR-CyberSec/VeeamDumper-BOF"
    assert e.repo_url == "https://github.com/MWR-CyberSec/VeeamDumper-BOF"
    assert e.who == "RalfHacker"
    assert e.circle_count == 3


def test_a_fork_event_takes_the_source_repo_not_the_fork_copy():
    """URL points at the fork; the repo worth surfacing is the left of the arrow."""
    e = parse_github_event(ev("gmh5225", "fork", "SunWeb3Sec/WDK-Guard"))
    assert e.action == "fork"
    assert e.repo == "SunWeb3Sec/WDK-Guard"


def test_action_falls_back_to_the_verb_when_the_tag_is_missing():
    e = parse_github_event(ev("a", "star", "o/r", tags=[]))
    assert e.action == "star"


def test_a_created_event_names_the_new_repo():
    e = parse_github_event(ev("alice", "created", "alice/newproject"))
    assert e.action == "created"
    assert e.repo == "alice/newproject"


def test_a_release_event_recovers_the_repo_from_the_deep_link():
    """github.com/owner/tool/releases/tag/v1.0 -> owner/tool."""
    e = parse_github_event(ev("bob", "release", "bob/tool"))
    assert e.action == "release"
    assert e.repo == "bob/tool"
    assert "v1.0" in e.text


def test_a_non_repo_event_is_dropped():
    """A follow / profile star has no owner-name path and must not create a bucket."""
    rec = ev("a", "star", "o/r", url="https://github.com/someuser")
    assert parse_github_event(rec) is None


def test_a_dirty_url_does_not_crash():
    assert parse_github_event(ev("a", "star", "o/r", url="not a url")) is None


# --------------------------------------------------------------- aggregation


def test_the_most_starred_repo_leads():
    records = [
        ev("p1", "star", "big/repo", minutes=1),
        ev("p2", "star", "big/repo", minutes=2),
        ev("p3", "star", "big/repo", minutes=3),
        ev("q1", "star", "small/repo", minutes=4),
    ]
    out = aggregate_github_day(records)

    assert out["trending"][0]["repo"] == "big/repo"
    assert out["trending"][0]["count"] == 3
    assert [s["login"] for s in out["trending"][0]["stargazers"]] == ["p1", "p2", "p3"]
    assert out["singles"][0]["repo"] == "small/repo"


def test_the_same_person_starring_twice_is_counted_once():
    records = [ev("p1", "star", "o/r"), ev("p1", "star", "o/r", minutes=5)]
    out = aggregate_github_day(records)
    repo = (out["trending"] + out["singles"])[0]
    assert repo["count"] == 1


def test_by_who_groups_each_followees_activity_most_active_first():
    records = [
        ev("busy", "star", "a/1"),
        ev("busy", "star", "b/2"),
        ev("busy", "fork", "c/3"),
        ev("quiet", "star", "d/4"),
    ]
    out = aggregate_github_day(records)

    assert out["by_who"][0]["login"] == "busy"
    assert out["by_who"][0]["count"] == 3
    assert {t["repo"] for t in out["by_who"][0]["touched"]} == {"a/1", "b/2", "c/3"}


def test_timeline_is_newest_first():
    records = [
        ev("a", "star", "o/1", minutes=10),
        ev("b", "star", "o/2", minutes=30),
        ev("c", "star", "o/3", minutes=20),
    ]
    out = aggregate_github_day(records)
    order = [e["repo"] for e in out["timeline"]]
    assert order == ["o/2", "o/3", "o/1"]


def test_stats_count_each_action_separately():
    records = [ev("a", "star", "o/1"), ev("b", "star", "o/2"), ev("c", "fork", "o/3")]
    out = aggregate_github_day(records)
    assert out["stats"] == {
        "total": 3,
        "repo_count": 3,
        "actor_count": 3,
        "star_count": 2,
        "fork_count": 1,
        "created_count": 0,
        "release_count": 0,
    }


def test_created_and_release_get_their_own_lane_not_the_star_buckets():
    """A create is not a vote for a repo; it must never be counted as a stargazer."""
    records = [
        ev("alice", "created", "alice/newproj", minutes=5),
        ev("bob", "release", "bob/tool", minutes=8),
        ev("carol", "star", "big/repo", minutes=1),
    ]
    out = aggregate_github_day(records)

    # Star buckets only hold the star.
    assert {r["repo"] for r in out["singles"]} == {"big/repo"}
    assert all(not r["stargazers"] or r["repo"] == "big/repo" for r in out["singles"])

    kinds = {h["action"]: h for h in out["highlights"]}
    assert set(kinds) == {"created", "release"}
    assert kinds["created"]["repo"] == "alice/newproj"
    assert kinds["created"]["who"] == "alice"
    assert out["stats"]["created_count"] == 1
    assert out["stats"]["release_count"] == 1
    # newest first
    assert out["highlights"][0]["action"] == "release"


def test_created_and_release_still_appear_in_timeline_and_by_who():
    records = [ev("alice", "created", "alice/newproj"), ev("bob", "release", "bob/tool")]
    out = aggregate_github_day(records)
    assert {e["action"] for e in out["timeline"]} == {"created", "release"}
    touched = {w["login"]: w["touched"][0]["action"] for w in out["by_who"]}
    assert touched == {"alice": "created", "bob": "release"}


def test_annotations_attach_by_repo_and_float_valuable_singles():
    records = [
        ev("a", "star", "gem/tool", minutes=1),
        ev("b", "star", "meh/thing", minutes=9),  # more recent, but not valued
    ]
    ann = {"gem/tool": {"one_liner": "红队 BOF", "topics": ["bof"], "tier": "must_see_page"}}
    out = aggregate_github_day(records, ann)

    # Both are single-star, so both are in `singles`; the valued one leads despite
    # being older.
    assert out["singles"][0]["repo"] == "gem/tool"
    assert out["singles"][0]["ann"]["one_liner"] == "红队 BOF"
    assert out["singles"][1]["ann"] == {}


def test_build_annotations_keys_by_repo_full_name():
    bundle = {
        "items": [
            {
                "url": "https://github.com/owner/repo",
                "one_liner": "x",
                "topics": ["t"],
                "tier": "recommend",
            },
            {"url": "https://github.com/someuser", "one_liner": "not a repo"},  # ignored
        ]
    }
    ann = build_annotations(bundle)
    assert ann == {"owner/repo": {"one_liner": "x", "topics": ["t"], "tier": "recommend"}}


def test_build_annotations_tolerates_no_bundle():
    assert build_annotations(None) == {}
