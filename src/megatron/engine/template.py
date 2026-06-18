from __future__ import annotations

from datetime import datetime, timezone

from jinja2 import Environment, StrictUndefined, select_autoescape
from jinja2.exceptions import TemplateError

from ..core.types import Item

_env = Environment(
    autoescape=select_autoescape(disabled_extensions=("txt",)),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)


def _format_item(item: Item, content_limit: int = 500) -> dict:
    return {
        "id": item.id,
        "source": item.source,
        "author": item.author,
        "author_name": item.author_name,
        "title": item.title,
        "content": item.content[:content_limit],
        "url": item.url,
        "published_at": item.published_at.isoformat(),
        "tags": item.tags,
        "links": item.links,
        "is_retweet": item.is_retweet,
        "metrics": item.metrics,
    }


def render_prompt(
    template_str: str,
    items: list[Item],
    extra_context: dict | None = None,
) -> str:
    """Render a Jinja2 prompt template with items + context.

    Available variables:
        items: list of normalized item dicts
        item_count: int
        top_items: first 30 items
        now: ISO timestamp
        ctx: extra_context dict
    """
    rendered_items = [_format_item(it) for it in items]
    ctx = {
        "items": rendered_items,
        "item_count": len(rendered_items),
        "top_items": rendered_items[:30],
        "now": datetime.now(timezone.utc).isoformat(),
        "ctx": extra_context or {},
    }
    try:
        tmpl = _env.from_string(template_str)
        return tmpl.render(**ctx)
    except TemplateError as e:
        raise ValueError(f"Template render error: {e}") from e


def preview_template(
    template_str: str,
    sample_items: list[Item] | None = None,
    extra_context: dict | None = None,
) -> str:
    """Render with sample data for UI preview without real items."""
    if sample_items is None:
        sample_items = []
    try:
        return render_prompt(template_str, sample_items, extra_context)
    except ValueError:
        return "[TEMPLATE ERROR] See logs"


__all__ = ["render_prompt", "preview_template"]
