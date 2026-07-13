"""The public frontend must show only public items, and only public items.

The load-bearing test is the privacy lock: nothing belonging to a held-back item
— not its headline, not its content, not the model's take on it — may appear in
any public response, and a `personal` source must not publish at all.

The take (`why_for_me`) of a *published* item is itself published: without it the
blog is a bare mirror of other people's tweets. Only `scores`, the model's
internal bookkeeping, stays behind.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from megatron.core.db import async_session_factory
from megatron.engine.bundle import BUNDLE_SCHEMA

SRC = "twitter_security_list"
DATE = "2026-07-12"

PUBLIC_LINE = "PUBLIC-CVE-DISCLOSURE"
PRIVATE_LINE = "PRIVATE-INTERNAL-THING"
PUBLIC_TAKE = "TAKE-ON-THE-PUBLIC-ONE"
PRIVATE_TAKE = "TAKE-ON-THE-PRIVATE-ONE"
SECRET_SCORE = 0.123456
PHOTO = "https://pbs.twimg.com/media/TESTPHOTO.jpg"


@pytest.fixture
def client():
    from megatron.web.app import app

    return TestClient(app)


def _bundle() -> dict:
    return {
        "schema": BUNDLE_SCHEMA,
        "date": DATE,
        "source_id": SRC,
        "title": "推特安全流",
        "items": [
            {
                "id": 1,
                "source_id": SRC,
                "external_id": "e1",
                "tier": "must_see_page",
                "one_liner": PUBLIC_LINE,
                "why_for_me": PUBLIC_TAKE,
                "scores": {"confidence": SECRET_SCORE},
                "topics": ["cve"],
                "url": "https://x.com/a/1",
                "content": "public tweet body",
                "public": True,
            },
            {
                "id": 2,
                "source_id": SRC,
                "external_id": "e2",
                "tier": "recommend",
                "one_liner": PRIVATE_LINE,
                "why_for_me": PRIVATE_TAKE,
                "topics": ["internal"],
                "url": "https://x.com/a/2",
                "content": "private body",
                "public": False,
            },
        ],
        "push_item_ids": [],
    }


@pytest_asyncio.fixture
async def a_public_day():
    """A completed run whose bundle has one public and one private item."""
    from megatron.core.engine_models import (
        AnalysisModule,
        AnalysisRun,
        LLMProvider,
        PromptTemplate,
    )
    from megatron.ingest.registry import sync_specs
    from megatron.ingest.spec import SourceSpec

    async with async_session_factory() as s:
        # The source must declare itself publishable — `audience` is the hard gate,
        # and a `personal` source (the default) never reaches the blog at all.
        await sync_specs(
            s,
            [SourceSpec(source_id=SRC, display_name="推特安全流", audience=["public"])],
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
        s.add_all([prompt, provider])
        await s.flush()
        module = AnalysisModule(
            name="m",
            source=SRC,
            prompt_template_id=prompt.id,
            provider_id=provider.id,
            filter_config={},
            agent_backend="none",
            tools_config=[],
            webhook_channel_ids=[],
        )
        s.add(module)
        await s.flush()
        s.add(AnalysisRun(module_id=module.id, status="completed", result=_bundle()))
        await s.commit()


def test_root_lands_on_the_public_frontend(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] in ("/zh", "/en")


def test_home_lists_a_day_that_has_public_content(client, a_public_day):
    r = client.get("/zh")
    assert r.status_code == 200
    assert SRC in r.text and DATE in r.text


def test_the_public_post_shows_public_and_hides_private(client, a_public_day):
    r = client.get(f"/zh/{SRC}/{DATE}")
    assert r.status_code == 200
    assert PUBLIC_LINE in r.text, "the public item is shown"
    assert PRIVATE_LINE not in r.text, "the private item must never appear"
    assert PRIVATE_TAKE not in r.text, "nor may the model's take on the private item"


def test_the_post_carries_the_models_take_but_not_its_scores(client, a_public_day):
    """The take is the point of the blog; the scores are internal bookkeeping."""
    r = client.get(f"/zh/{SRC}/{DATE}")
    assert PUBLIC_TAKE in r.text, "the model's take on a published item is published"
    assert str(SECRET_SCORE) not in r.text, "internal scores stay internal"


def test_a_day_with_no_public_content_is_404(client):
    # No bundle seeded → nothing public → 404 (not 403).
    assert client.get(f"/zh/{SRC}/2026-01-01").status_code == 404


# --- tier filter (CSS-only) -------------------------------------------------


def test_the_post_offers_a_tab_per_tier_it_actually_has(client, a_public_day):
    r = client.get(f"/zh/{SRC}/{DATE}")
    assert 'id="tf-all"' in r.text
    # The bundle's only public item is must_see_page, so that is the only tier tab.
    assert 'id="tf-must_see_page"' in r.text
    assert 'id="tf-recommend"' not in r.text, "no tab for a tier with nothing in it"


def test_filtering_hides_with_css_and_never_drops_items_from_the_page(client, a_public_day):
    """The tabs must not be a server-side filter: a crawler (and a reader with no
    :has() support) has to see the whole day in one document."""
    r = client.get(f"/zh/{SRC}/{DATE}")
    assert PUBLIC_LINE in r.text, "every public item is in the DOM regardless of tab"
    assert ":has(#tf-must_see_page:checked)" in r.text, "switching is pure CSS"
    assert "<script" not in r.text, "the public pages stay JS-free"


# --- home card: tier breakdown + picture -------------------------------------


@pytest_asyncio.fixture
async def a_photo_for_the_lead(a_public_day):
    """The `items` row the render-time media lookup reads. The bundle has no media
    of its own — that is the whole point of looking it up at render time."""
    from megatron.core.models import ItemRecord

    now = datetime.now(timezone.utc)
    async with async_session_factory() as s:
        s.add(
            ItemRecord(
                item_id="e1",  # the public item's external_id
                source=SRC,
                url="https://x.com/a/1",
                content="public tweet body",
                published_at=now,
                collected_at=now,
                media={"photos": [PHOTO], "videos": []},
            )
        )
        await s.commit()


def test_the_home_card_breaks_the_count_down_by_tier(client, a_public_day):
    r = client.get("/zh")
    assert "必看" in r.text and "tierdot" in r.text


def test_the_home_card_shows_a_proxied_picture_never_a_hotlink(client, a_photo_for_the_lead):
    """Hotlinking pbs.twimg.com would be a broken image for the blog's readers and
    would hand their IP to Twitter. The src must point at our own origin."""
    r = client.get("/zh")
    assert 'src="/img/' in r.text
    assert "pbs.twimg.com" not in r.text


def test_a_day_whose_items_have_no_photo_renders_no_image(client, a_public_day):
    """No media row → no <img> at all. The grid's `auto` column collapses and the
    card is the text-only one it was before."""
    r = client.get("/zh")
    assert '<span class="p-thumb">' not in r.text  # the markup; the CSS class always exists
    assert "<img" not in r.text


def test_both_languages_render(client, a_public_day):
    assert client.get(f"/en/{SRC}/{DATE}").status_code == 200
    assert client.get(f"/zh/{SRC}/{DATE}").status_code == 200


def test_the_personal_capability_page_is_unchanged(client, a_public_day):
    # /day/... still requires the token; a wrong/absent one 404s (private world).
    assert client.get(f"/day/{SRC}/{DATE}").status_code in (404, 422)


def test_admin_still_requires_login(client):
    assert client.get("/ui/dashboard", follow_redirects=False).status_code in (303, 307)


@pytest_asyncio.fixture
async def a_personal_source_day():
    """A day from a `personal` source whose items the model marked public.

    The GitHub feed is the real case: every event in it is public GitHub activity,
    yet publishing the stream exposes *who you follow*. The leak is the curation,
    not the item — so the gate is the source, and it must hold even when the model
    marks every item public.
    """
    from megatron.core.engine_models import (
        AnalysisModule,
        AnalysisRun,
        LLMProvider,
        PromptTemplate,
    )
    from megatron.ingest.registry import sync_specs
    from megatron.ingest.spec import SourceSpec

    gh = "github_followee_feed"
    async with async_session_factory() as s:
        await sync_specs(
            s, [SourceSpec(source_id=gh, display_name="GitHub 关注流", audience=["personal"])]
        )
        prompt = PromptTemplate(name="p2", template="x", output_schema={})
        provider = LLMProvider(name="p2", model="m", api_base="http://x", api_key="k")
        s.add_all([prompt, provider])
        await s.flush()
        module = AnalysisModule(
            name="m2", source=gh, prompt_template_id=prompt.id, provider_id=provider.id
        )
        s.add(module)
        await s.flush()
        s.add(
            AnalysisRun(
                module_id=module.id,
                status="completed",
                result={
                    "schema": BUNDLE_SCHEMA,
                    "date": DATE,
                    "source_id": gh,
                    "title": "GitHub 关注流",
                    "items": [
                        {
                            "id": 1,
                            "source_id": gh,
                            "tier": "must_see_page",
                            "one_liner": "FOLLOW-GRAPH-LEAK",
                            "topics": ["gh"],
                            "url": "https://github.com/a/b",
                            "content": "x starred y",
                            "public": True,  # the model says publish...
                        }
                    ],
                    "push_item_ids": [],
                },
            )
        )
        await s.commit()
    return gh


def test_a_personal_source_never_reaches_the_blog(client, a_personal_source_day):
    """...and the source's `audience` overrules it. Nothing, anywhere, publicly."""
    gh = a_personal_source_day

    # No article page.
    assert client.get(f"/zh/{gh}/{DATE}").status_code == 404
    # Not listed on the index either — and its text appears nowhere.
    home = client.get("/zh")
    assert home.status_code == 200
    assert gh not in home.text
    assert "FOLLOW-GRAPH-LEAK" not in home.text
