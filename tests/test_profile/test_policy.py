"""Filtering policy is a file (config/policy.yaml), not a framework constant."""

from __future__ import annotations

from megatron.profile.policy import load_policy


def test_the_shipped_policy_has_the_product_caps_and_politics_list():
    pol = load_policy("config/policy.yaml")
    assert pol["caps"]["must_see_max"] == 8
    assert pol["caps"]["lead_min"] == 3
    assert "政治" in pol["politics_blocklist"]
    assert "election" in pol["politics_blocklist"]


def test_a_missing_policy_file_falls_back_to_neutral_not_a_crash(tmp_path):
    pol = load_policy(str(tmp_path / "nope.yaml"))
    assert pol["politics_blocklist"] == [], "no file = no politics filtering, not a baked-in list"
    assert all(v == 0 for v in pol["caps"].values()), "neutral caps trim nothing"


def test_bundle_ships_a_neutral_fallback_not_the_product_policy():
    """The product's real values must live in the file, not back in code."""
    from megatron.engine.bundle import DEFAULT_CAPS, POLITICS

    assert POLITICS == (), "the political blocklist is config, not a framework constant"
    assert all(v == 0 for v in DEFAULT_CAPS.values()), "DEFAULT_CAPS is a neutral fallback"


def test_source_page_layout_projects_into_the_config_blob():
    """Template selection is data-driven: the layout rides in source config."""
    from megatron.ingest.spec import SourceSpec

    feed = SourceSpec(source_id="s_a", adapter="http_push", page_layout="feed")
    assert feed.db_config()["page_layout"] == "feed"
    default = SourceSpec(source_id="s_b", adapter="http_push")
    assert default.db_config()["page_layout"] == "digest"
