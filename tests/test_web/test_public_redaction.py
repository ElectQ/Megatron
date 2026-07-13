"""A public source can still keep a secret: the GitHub feed publishes the repo,
never who starred it.

Each event — "odzhan starred kernullist/KnWin32ApiMonitor" — is individually
public, but the day as a whole is the reader's follow graph. So the stream is
published with the *who* redacted: `author` and the raw `content` (which spells
the login out) are dropped on the public projection, while the model's
`one_liner`/`why_for_me` — phrased by the prompt without names — carry the value.

The load-bearing assertion is simply: the login must not appear in any public
response. The token-gated day page is unaffected — it renders the raw bundle and
never goes through this projection — so it stays the owner's full-detail view.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from megatron.core.db import async_session_factory
from megatron.engine.bundle import BUNDLE_SCHEMA
from megatron.web.public_view import EMPTY, Policy, load_policy, public_items

GH = "github_followee_feed"
DATE = "2026-07-13"

LOGIN = "odzhan"  # a person the reader follows — must never surface publicly
RAW = f"{LOGIN} starred kernullist/KnWin32ApiMonitor"
REPO_LINE = "KnWin32ApiMonitor:Win32 API 监控工具（3 人 star）"  # the model's take, name-free


def _bundle():
    return {
        "schema": BUNDLE_SCHEMA,
        "source_id": GH,
        "date": DATE,
        "title": "GitHub 关注流",
        "items": [
            {
                "id": 1,
                "source_id": GH,
                "external_id": "event:1",
                "tier": "must_see_page",
                "one_liner": REPO_LINE,
                "why_for_me": "多人汇聚,值得看的 Windows 监控工具。",
                "topics": ["工具", "windows"],
                "author": LOGIN,
                "author_name": LOGIN,
                "content": RAW,
                "url": "https://github.com/kernullist/KnWin32ApiMonitor",
                "public": None,
            }
        ],
        "push_item_ids": [],
    }


@pytest.fixture
def client():
    from megatron.web.app import app

    return TestClient(app)


@pytest_asyncio.fixture
async def a_redacted_github_day():
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
        s.add_all([prompt, provider])
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


# --- unit: the projection drops the who, keeps the what -----------------------


def test_redaction_drops_author_and_raw_content_keeps_the_take():
    policy = Policy(audience={GH: "public"}, redact={GH})
    (item,) = public_items(_bundle(), policy)
    assert "author" not in item and "author_name" not in item
    assert "content" not in item, "the raw 'X starred Y' embeds the login"
    assert item["one_liner"] == REPO_LINE  # the name-free take survives
    assert item["why_for_me"] and item["topics"]


def test_without_the_redact_flag_the_same_source_would_keep_them():
    """Proves the redaction is the flag's doing, not something incidental."""
    policy = Policy(audience={GH: "public"})  # public, but NOT redacting
    (item,) = public_items(_bundle(), policy)
    assert item["author"] == LOGIN and item["content"] == RAW


@pytest.mark.asyncio
async def test_the_flag_is_loaded_from_the_source_config(a_redacted_github_day):
    """End of the wire: the YAML flag → SourceConfig column → Policy.redact."""
    async with async_session_factory() as s:
        policy = await load_policy(s)
    assert policy.source_redacts(GH)


# --- end to end: the login must not appear in any public response -------------


def test_the_public_page_shows_the_repo_but_not_who_starred_it(client, a_redacted_github_day):
    r = client.get(f"/zh/{GH}/{DATE}")
    assert r.status_code == 200
    assert "KnWin32ApiMonitor" in r.text, "the repo and the model's take are published"
    assert LOGIN not in r.text, "the follower's login must never reach the public page"
    assert RAW not in r.text, "nor the raw event text that embeds it"


def test_redaction_is_opt_in_the_default_policy_redacts_nothing():
    """A Twitter-style public source keeps its author — redaction is per-source."""
    assert not EMPTY.source_redacts(GH)
    assert not Policy(audience={GH: "public"}).source_redacts(GH)
