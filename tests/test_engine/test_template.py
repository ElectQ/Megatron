from __future__ import annotations

from datetime import datetime, timezone

from megatron.core.types import Item
from megatron.engine.template import preview_template, render_prompt


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
