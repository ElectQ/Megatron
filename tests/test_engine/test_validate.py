"""The output schema is advisory (it never fails a run) — so it has to be honest.

A schema that flags things the model was right to do trains you to ignore it.
"""

from __future__ import annotations

from megatron.engine.builtin import DAILY_INTEL_V1_SCHEMA
from megatron.engine.validate import validate_output


def _keep(**over) -> dict:
    item = {
        "external_id": "e1",
        "source_id": "s1",
        "tier": "skim",
        "one_liner": "一句话",
        "why_for_me": "和你有关",
        "topics": ["rce", "cve"],
    }
    item.update(over)
    return {"items": [item]}


def test_a_good_answer_has_no_complaints():
    assert validate_output(_keep(), DAILY_INTEL_V1_SCHEMA) == []


def test_a_drop_needs_only_its_verdict():
    """Two thirds of a 144-item day are drops. Making the model write a summary
    for each one is output tokens spent on things nobody will ever see."""
    doc = {"items": [{"external_id": "e1", "source_id": "s1", "tier": "drop"}]}
    assert validate_output(doc, DAILY_INTEL_V1_SCHEMA) == []


def test_a_promoted_card_must_say_why_it_was_promoted():
    """ "why this matters to *you*" is the whole product. A recommend without it
    is just a headline."""
    errors = validate_output(
        {"items": [{"external_id": "e", "source_id": "s", "tier": "recommend"}]},
        DAILY_INTEL_V1_SCHEMA,
    )
    assert any("one_liner" in e for e in errors)
    assert any("why_for_me" in e for e in errors)


def test_what_a_card_owes_the_reader_scales_with_how_big_it_renders():
    """A skim line is a title and a chip. Demanding a personal note for it just
    produces filler — and a schema that flags honest work gets ignored."""
    skim = {
        "external_id": "e",
        "source_id": "s",
        "tier": "skim",
        "one_liner": "x",
        "topics": ["cve"],
    }
    assert validate_output({"items": [skim]}, DAILY_INTEL_V1_SCHEMA) == []

    promoted = {**skim, "tier": "recommend", "why_for_me": "y"}
    assert validate_output({"items": [promoted]}, DAILY_INTEL_V1_SCHEMA), "one tag is not tagging"


def test_a_skim_line_still_needs_to_say_something():
    naked = {"external_id": "e", "source_id": "s", "tier": "skim"}
    assert validate_output({"items": [naked]}, DAILY_INTEL_V1_SCHEMA)


def test_an_invented_tier_is_caught():
    errors = validate_output(_keep(tier="urgent!!"), DAILY_INTEL_V1_SCHEMA)
    assert any("tier" in e for e in errors)


def test_no_schema_means_no_opinion():
    assert validate_output({"anything": 1}, {}) == []
