"""A follow item's `persona` blob must survive ingest into ItemRecord.raw.

IngestItem is `extra="ignore"`, so a top-level field the schema does not declare
is silently dropped. `persona` is declared and folded into `raw` on the way in —
this pins that, so the day page's newcomer board actually has data to render.
"""

from __future__ import annotations

from megatron.ingest.schemas import IngestEnvelope, envelope_to_items

PERSONA = {"login": "thecodacus", "followers": 1200, "top_repos": [{"name": "x/y", "stars": 9}]}


def _one(item: dict):
    env = IngestEnvelope(schema_version=1, source_id="github_followee_feed", items=[item])
    return envelope_to_items(env, source_id="github_followee_feed", collect_date="2026-07-13")[0]


def test_persona_is_folded_into_raw():
    item = _one(
        {
            "external_id": "follow:a:thecodacus",
            "content": "a followed thecodacus",
            "tags": ["kind:follow"],
            "persona": PERSONA,
        }
    )
    assert item.raw["persona"]["login"] == "thecodacus"
    assert item.raw["persona"]["followers"] == 1200


def test_an_item_without_persona_gets_no_persona_key():
    item = _one({"external_id": "e1", "content": "a starred x/y", "tags": ["kind:star"]})
    assert "persona" not in item.raw


def test_a_pre_existing_raw_is_preserved_alongside_persona():
    item = _one(
        {
            "external_id": "e1",
            "content": "x",
            "raw": {"orig": 1},
            "persona": PERSONA,
        }
    )
    assert item.raw["orig"] == 1 and item.raw["persona"]["login"] == "thecodacus"
