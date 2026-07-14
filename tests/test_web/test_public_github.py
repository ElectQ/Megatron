"""The GitHub feed's public face is a repo board, and it has no people on it.

The private page answers "what is my circle doing" — stargazer avatars, an actor
timeline, a by-person column. Every one of those is the follow graph, so the
public page is not that page with the names blanked out: it is built from the
same day and never assembles them at all.

What survives redaction is the convergence count — "four accounts starred it" —
which is both the stream's whole value and nobody's identity. These tests pin
that: the count is published, and no login reaches the response by any route.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from megatron.core.db import async_session_factory
from megatron.core.models import ItemRecord
from megatron.engine.bundle import BUNDLE_SCHEMA
from megatron.web.public_github import public_github_view
from megatron.web.public_view import EMPTY, Policy

GH = "github_followee_feed"
DATE = "2026-07-13"
T0 = datetime(2026, 7, 13, 9, 30, tzinfo=timezone.utc)

# Three people the reader follows. Not one of these strings may appear publicly.
ALICE, BOB, CAROL = "odzhan", "hasherezade", "gynvael"

HOT = "ghostty-org/ghostty"  # converged: two stars + a fork
ONE = "google/osv-scanner"  # a single star
NEW = "rustsec/cargo-audit-action"  # created
REL = "sigstore/cosign"  # released
HELD = "acme/internal-thing"  # the model held it back

POLICY = Policy(audience={GH: "public"}, redact={GH})


def _star(who: str, repo: str, minutes: int = 0) -> ItemRecord:
    return ItemRecord(
        item_id=f"star:{who}:{repo}",
        source=GH,
        content=f"{who} starred {repo}",
        url=f"https://github.com/{repo}",
        author=who,
        author_name=who,
        published_at=T0,
        collected_at=T0,
        collect_date=DATE,
        tags=["kind:star"],
        links=[f"https://github.com/{who}"],
    )


def _fork(who: str, repo: str) -> ItemRecord:
    forkee = f"{who}/{repo.split('/')[-1]}"
    return ItemRecord(
        item_id=f"fork:{who}:{repo}",
        source=GH,
        content=f"{who} forked {repo} → {forkee}",
        url=f"https://github.com/{forkee}",
        author=who,
        author_name=who,
        published_at=T0,
        collected_at=T0,
        collect_date=DATE,
        tags=["kind:fork"],
    )


def _created(who: str, repo: str) -> ItemRecord:
    return ItemRecord(
        item_id=f"created:{who}:{repo}",
        source=GH,
        content=f"{who} created repository {repo}",
        url=f"https://github.com/{repo}",
        author=who,
        author_name=who,
        published_at=T0,
        collected_at=T0,
        collect_date=DATE,
        tags=["kind:created"],
    )


def _released(who: str, repo: str, tag: str) -> ItemRecord:
    return ItemRecord(
        item_id=f"release:{who}:{repo}",
        source=GH,
        content=f"{who} released {tag} of {repo}",
        url=f"https://github.com/{repo}/releases/tag/{tag}",
        author=who,
        author_name=who,
        published_at=T0,
        collected_at=T0,
        collect_date=DATE,
        tags=["kind:release"],
    )


def _follow(actor: str, target: str) -> ItemRecord:
    """A follow row. The persona is the *target's* public profile."""
    return ItemRecord(
        item_id=f"follow:{actor}:{target}",
        source=GH,
        content=f"{actor} followed {target}",
        url=f"https://github.com/{target}",
        author=actor,
        author_name=actor,
        published_at=T0,
        collected_at=T0,
        collect_date=DATE,
        tags=["kind:follow"],
        raw={
            "persona": {
                "login": target,
                "name": "Marta Kowalczyk",
                "bio": "Kernel exploitation, eBPF verifier bugs.",
                "followers": 4200,
                "languages": ["C", "Rust"],
                "top_repos": [{"name": f"{target}/bpf-fuzz", "stars": 1800}],
            }
        },
    )


def _rows() -> list[ItemRecord]:
    return [
        _star(ALICE, HOT),
        _star(BOB, HOT),
        _fork(CAROL, HOT),
        _star(ALICE, ONE),
        _star(BOB, HELD),
        _created(CAROL, NEW),
        _released(ALICE, REL, "v2.6.0"),
        _follow(BOB, "mkowal"),
    ]


def _bundle(held_public: bool | None = False) -> dict:
    """The analysis: annotations for the repos, and a verdict on the held-back one."""
    return {
        "schema": BUNDLE_SCHEMA,
        "source_id": GH,
        "date": DATE,
        "title": "GitHub 关注流",
        "items": [
            {
                "id": 1,
                "source_id": GH,
                "external_id": "e1",
                "tier": "must_see_page",
                "one_liner": "GPU 加速终端",
                "topics": ["terminal", "zig"],
                "url": f"https://github.com/{HOT}",
                "author": ALICE,
                "content": f"{ALICE} starred {HOT}",
                "public": None,
            },
            {
                "id": 2,
                "source_id": GH,
                "external_id": "e2",
                "tier": "skim",
                "one_liner": "SENSITIVE-TAKE",
                "topics": [],
                "url": f"https://github.com/{HELD}",
                "author": BOB,
                "content": f"{BOB} starred {HELD}",
                "public": held_public,
            },
        ],
        "push_item_ids": [],
    }


def _view(**kw):
    kw.setdefault("policy", POLICY)
    return public_github_view(
        _rows(), _bundle(), source_id=GH, date=DATE, title="GitHub 关注流", **kw
    )


# --- the projection: counts survive, identities are never built ----------------


def test_no_login_appears_anywhere_in_the_view():
    """The load-bearing one: serialize the whole context and grep it."""
    blob = json.dumps(_view(), ensure_ascii=False)
    for login in (ALICE, BOB, CAROL):
        assert login not in blob, f"{login} reached the public projection"


def test_the_converged_repo_leads_and_its_count_folds_in_the_fork():
    view = _view()
    assert view["lead"]["repo"] == HOT
    assert view["lead"]["count"] == 3, "two stars + one fork — a fork is convergence too"
    assert view["lead"]["one_liner"] == "GPU 加速终端"


def test_a_single_star_repo_is_not_dressed_up_as_converged():
    view = _view()
    assert [r["repo"] for r in view["singles"]] == [ONE]
    assert view["singles"][0]["count"] == 1


def test_a_held_back_item_takes_its_whole_repo_off_the_page():
    """The board is built from rows, so hiding an *item* has to hide its repo."""
    blob = json.dumps(_view(), ensure_ascii=False)
    assert HELD not in blob
    assert "SENSITIVE-TAKE" not in blob, "nor the model's take on it"


def test_publishing_that_same_item_puts_the_repo_back():
    """Proves the removal is the verdict's doing, not an accident of aggregation."""
    view = public_github_view(
        _rows(),
        _bundle(held_public=True),
        source_id=GH,
        date=DATE,
        title="t",
        policy=POLICY,
    )
    assert HELD in [r["repo"] for r in view["singles"]]


def test_highlights_keep_the_release_tag_and_drop_the_releaser():
    view = _view()
    release = next(h for h in view["highlights"] if h["action"] == "release")
    assert release["repo"] == REL
    assert release["ref"] == "v2.6.0", "the tag comes from the URL, not from the sentence"
    assert "who" not in release and "text" not in release


def test_stats_do_not_measure_the_circle():
    """`actor_count`/`star_count` would size the reader's follow list. Not published."""
    stats = _view()["stats"]
    assert set(stats) == {
        "repo_count",
        "converged_count",
        "created_count",
        "release_count",
        "newcomer_count",
    }
    assert stats["repo_count"] == 2 and stats["converged_count"] == 1


def test_topics_are_counted_for_the_rail():
    assert {"name": "terminal", "count": 1} in _view()["topics"]


# --- newcomers: safe by construction, still opt-out ----------------------------


def test_newcomers_carry_the_followed_not_the_follower():
    (newcomer,) = _view()["newcomers"]
    assert newcomer["target"] == "mkowal"
    assert newcomer["persona"]["followers"] == 4200
    assert BOB not in json.dumps(newcomer), "the actor who followed them is not carried"


def test_a_source_can_switch_the_newcomer_lane_off():
    view = _view(show_newcomers=False)
    assert view["newcomers"] == [] and view["stats"]["newcomer_count"] == 0


def test_a_personal_source_publishes_nothing_through_this_view():
    """The hard gate still belongs to public_view; held_back_repos honours it."""
    view = public_github_view(_rows(), _bundle(), source_id=GH, date=DATE, title="t", policy=EMPTY)
    assert view["lead"] is None and view["singles"] == []


# --- end to end ---------------------------------------------------------------


@pytest.fixture
def client():
    from megatron.web.app import app

    return TestClient(app)


@pytest_asyncio.fixture
async def a_published_github_day():
    from megatron.core.engine_models import (
        AnalysisModule,
        AnalysisRun,
        LLMProvider,
        PromptTemplate,
    )
    from megatron.ingest.registry import sync_specs
    from megatron.ingest.spec import SourceSpec

    async with async_session_factory() as s:
        await sync_specs(
            s,
            [
                SourceSpec(
                    source_id=GH,
                    display_name="GitHub 关注流",
                    audience=["public"],
                    public_redact=True,
                    page_layout="feed",
                )
            ],
        )
        prompt = PromptTemplate(name="p", template="x", output_schema={})
        provider = LLMProvider(
            name="p",
            model="m",
            api_base="http://x",
            api_key="k",
            temperature=0.3,
            max_tokens=1000,
            enabled=True,
        )
        s.add_all([prompt, provider, *_rows()])
        await s.flush()
        module = AnalysisModule(
            name="m",
            source=GH,
            prompt_template_id=prompt.id,
            provider_id=provider.id,
            filter_config={},
            agent_backend="none",
            tools_config=[],
            webhook_channel_ids=[],
        )
        s.add(module)
        await s.flush()
        now = datetime.now(timezone.utc)
        s.add(
            AnalysisRun(
                module_id=module.id,
                status="completed",
                result=_bundle(),
                started_at=now,
                finished_at=now,
            )
        )
        await s.commit()


def test_the_public_page_is_the_repo_board(client, a_published_github_day):
    r = client.get(f"/zh/{GH}/{DATE}")
    assert r.status_code == 200
    assert "ghostty" in r.text, "the repo is published"
    assert "×3" in r.text, "and so is how many accounts converged on it"
    assert "今日聚焦" in r.text, "the board's own layout, not the tiered digest"


def test_not_one_login_reaches_the_public_response(client, a_published_github_day):
    r = client.get(f"/zh/{GH}/{DATE}")
    for login in (ALICE, BOB, CAROL):
        assert login not in r.text, f"{login} leaked onto the public page"
    assert "starred" not in r.text, "nor the raw event sentence that embeds a login"


def test_the_held_back_repo_is_absent_end_to_end(client, a_published_github_day):
    r = client.get(f"/zh/{GH}/{DATE}")
    assert HELD not in r.text and "SENSITIVE-TAKE" not in r.text
