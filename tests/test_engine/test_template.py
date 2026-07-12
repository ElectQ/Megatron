from __future__ import annotations

import functools
from datetime import datetime, timezone

from megatron.core.types import Item
from megatron.engine.template import preview_template, render_prompt


@functools.lru_cache
def _prompt_body(name: str) -> str:
    """The shipped prompt body, loaded from config/prompts/ (its real home)."""
    from megatron.profile.loader import load_prompt_specs

    specs, _ = load_prompt_specs("config/prompts")
    return next(s.body for s in specs if s.name == name)


def _make_item(content="test content", author="user"):
    return Item(
        id="t1",
        source="twitter",
        source_ref="list1",
        content=content,
        url="https://x.com/u/status/t1",
        author=author,
        author_name="User",
        published_at=datetime(2026, 6, 17, tzinfo=timezone.utc),
        collected_at=datetime(2026, 6, 17, tzinfo=timezone.utc),
        metrics={"like_count": 5, "retweet_count": 2, "reply_count": 0, "view_count": 100},
    )


def test_render_basic_template():
    tmpl = "Count: {{ item_count }}\n{% for t in top_items %}- {{ t.author }}: {{ t.content }}\n{% endfor %}"
    rendered = render_prompt(tmpl, [_make_item("CVE-2024-1", "alice")])
    assert "Count: 1" in rendered
    assert "alice" in rendered
    assert "CVE-2024-1" in rendered


def test_render_empty_items():
    tmpl = "Items: {{ item_count }}"
    rendered = render_prompt(tmpl, [])
    assert "Items: 0" in rendered


def test_render_with_context():
    tmpl = "Window: {{ ctx.window_hours }}h"
    rendered = render_prompt(tmpl, [], {"window_hours": 24})
    assert "Window: 24h" in rendered


def test_preview_invalid_template_does_not_raise():
    bad = "{{ undefined_var_in_strict }}"
    rendered = preview_template(bad)
    assert "TEMPLATE ERROR" in rendered


# --------------------------------------------- the shipped prompts must render


def test_daily_intel_renders_without_any_context():
    """Rendering is StrictUndefined: `ctx.foo` on an empty ctx raises.

    The prompt ships as a file seed, so a template that only renders when the
    runner hands it a full context would fail on the first real run — and in the
    preview pane, which passes none.
    """
    rendered = render_prompt(_prompt_body("daily_intel_v1"), [_make_item()])
    assert "must_see_push" in rendered
    assert "至少 3 条" in rendered, "falls back to the default caps"
    assert "5 - 8 条" in rendered


def test_daily_intel_states_the_caps_it_will_actually_be_held_to():
    rendered = render_prompt(
        _prompt_body("daily_intel_v1"),
        [_make_item()],
        {"caps": {"lead_min": 1, "must_see_min": 2, "must_see_max": 4}},
    )
    assert "至少 1 条" in rendered
    assert "2 - 4 条" in rendered


def test_github_radar_renders_without_any_context():
    rendered = render_prompt(_prompt_body("github_radar_v1"), [_make_item()])
    assert "GitHub 关注雷达" in rendered
    assert "circle_count" in rendered, "the convergence signal is explained"
    assert "这个源不推送" in rendered


def test_every_prompt_files_output_schema_resolves():
    """A prompt file's `output_schema:` must name a schema the engine knows."""
    from megatron.engine.builtin import SCHEMAS
    from megatron.profile.loader import load_prompt_specs

    specs, errors = load_prompt_specs("config/prompts")
    assert not errors
    assert specs, "the profile ships prompt files"
    for s in specs:
        assert s.output_schema in SCHEMAS, f"{s.name} → unknown schema {s.output_schema!r}"


def test_daily_intel_takes_the_intent_from_the_task():
    rendered = render_prompt(
        _prompt_body("daily_intel_v1"),
        [_make_item()],
        {"intent": {"primary": ["供应链安全"], "secondary": ["取证"]}},
    )
    assert "首要：供应链安全" in rendered
    assert "次要：取证" in rendered
