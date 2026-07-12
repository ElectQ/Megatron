"""The public frontend must show only public items and never leak personal analysis.

The load-bearing test is the privacy lock: a private item's text must not appear
in any public response, and `why_for_me` must never render publicly.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from megatron.core.db import async_session_factory
from megatron.engine.bundle import BUNDLE_SCHEMA

SRC = "twitter_security_list"
DATE = "2026-07-12"

PUBLIC_LINE = "PUBLIC-CVE-DISCLOSURE"
PRIVATE_LINE = "PRIVATE-INTERNAL-THING"
PERSONAL_WHY = "WHY-THIS-MATTERS-TO-YOU"


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
                "why_for_me": PERSONAL_WHY,
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
                "why_for_me": PERSONAL_WHY,
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
        await sync_specs(s, [SourceSpec(source_id=SRC, display_name="推特安全流")])
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
    assert PERSONAL_WHY not in r.text, "personal 'why for you' is never public"


def test_a_day_with_no_public_content_is_404(client):
    # No bundle seeded → nothing public → 404 (not 403).
    assert client.get(f"/zh/{SRC}/2026-01-01").status_code == 404


def test_both_languages_render(client, a_public_day):
    assert client.get(f"/en/{SRC}/{DATE}").status_code == 200
    assert client.get(f"/zh/{SRC}/{DATE}").status_code == 200


def test_the_personal_capability_page_is_unchanged(client, a_public_day):
    # /day/... still requires the token; a wrong/absent one 404s (private world).
    assert client.get(f"/day/{SRC}/{DATE}").status_code in (404, 422)


def test_admin_still_requires_login(client):
    assert client.get("/ui/dashboard", follow_redirects=False).status_code in (303, 307)
