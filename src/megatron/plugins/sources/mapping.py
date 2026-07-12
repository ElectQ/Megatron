"""Path-expression mapping from an arbitrary payload to Items.

A deliberately small JSONPath subset — enough to describe real feeds, small
enough to read in one sitting and to give precise errors:

    $               the element itself
    $.a.b           dict traversal
    $.a[0]          list index
    $.a[*].b        flatten: collect `b` from every element of `a`
    anything else   a literal constant

Scalar expressions are evaluated against a single item; `map.items` is evaluated
against the response root and must produce a list.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from ...core.logging import get_logger
from ...core.types import Item
from ...ingest.spec import MapSpec

logger = get_logger(__name__)

_SEG_RE = re.compile(r"([^.\[\]]+)|\[(\*|\d+)\]")


class MappingError(ValueError):
    """A path expression could not be applied to the payload."""


def _segments(expr: str) -> list[str]:
    body = expr[1:]  # strip leading '$'
    body = body.lstrip(".")
    if not body:
        return []
    out: list[str] = []
    for key, idx in _SEG_RE.findall(body):
        out.append(key if key else f"[{idx}]")
    return out


def resolve_path(data: Any, expr: str) -> Any:
    """Evaluate a `$...` expression. Returns None when the path does not exist."""
    if not expr.startswith("$"):
        return expr  # constant
    cur: Any = data
    for seg in _segments(expr):
        if cur is None:
            return None
        if seg == "[*]":
            if not isinstance(cur, list):
                return None
            cur = cur  # flatten handled by the caller collecting from a list
            continue
        if seg.startswith("[") and seg.endswith("]"):
            idx = int(seg[1:-1])
            if not isinstance(cur, list) or idx >= len(cur):
                return None
            cur = cur[idx]
            continue
        if isinstance(cur, list):
            # `a[*].b` — after a wildcard, project the key across the list.
            cur = [x.get(seg) if isinstance(x, dict) else None for x in cur]
            continue
        if not isinstance(cur, dict):
            return None
        cur = cur.get(seg)
    return cur


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    return str(value)


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return [v for v in value if v is not None]
    return [value]


def parse_dt(value: Any) -> datetime:
    """Best-effort timestamp parsing; falls back to now(UTC)."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):  # epoch seconds
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return datetime.now(timezone.utc)
    text = _as_str(value).strip()
    if not text:
        return datetime.now(timezone.utc)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",  # RFC 822 (RSS)
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(text, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return datetime.now(timezone.utc)


def extract_items(payload: Any, spec: MapSpec) -> list[Any]:
    """Pull the item array out of a response body."""
    raw = resolve_path(payload, spec.items)
    if raw is None:
        raise MappingError(
            f"map.items '{spec.items}' matched nothing in the response "
            f"(top-level keys: {list(payload)[:8] if isinstance(payload, dict) else type(payload).__name__})"
        )
    if not isinstance(raw, list):
        raise MappingError(
            f"map.items '{spec.items}' must resolve to a list, got {type(raw).__name__}"
        )
    return raw


def map_item(raw: Any, spec: MapSpec, *, source_id: str, collect_date: str) -> Item | None:
    """Apply the field expressions to one raw element. None = unusable (no id)."""
    external_id = _as_str(resolve_path(raw, spec.external_id)).strip()
    if not external_id:
        return None

    published = (
        parse_dt(resolve_path(raw, spec.published_at))
        if spec.published_at
        else datetime.now(timezone.utc)
    )
    metrics = {k: resolve_path(raw, expr) for k, expr in spec.metrics.items()}

    return Item(
        id=external_id,
        source=source_id,
        source_ref="",
        title=_as_str(resolve_path(raw, spec.title)) if spec.title else "",
        content=_as_str(resolve_path(raw, spec.content)) if spec.content else "",
        url=_as_str(resolve_path(raw, spec.url)) if spec.url else "",
        author=_as_str(resolve_path(raw, spec.author)) if spec.author else "",
        author_name=_as_str(resolve_path(raw, spec.author_name)) if spec.author_name else "",
        language=_as_str(resolve_path(raw, spec.language)) if spec.language else "",
        published_at=published,
        collected_at=datetime.now(timezone.utc),
        collect_date=collect_date,
        tags=[_as_str(t) for t in _as_list(resolve_path(raw, spec.tags))] if spec.tags else [],
        links=[_as_str(x) for x in _as_list(resolve_path(raw, spec.links))] if spec.links else [],
        metrics={k: v for k, v in metrics.items() if v is not None},
        raw=raw if isinstance(raw, dict) else {"value": raw},
    )


def map_payload(payload: Any, spec: MapSpec, *, source_id: str, collect_date: str) -> list[Item]:
    items: list[Item] = []
    skipped = 0
    for raw in extract_items(payload, spec):
        item = map_item(raw, spec, source_id=source_id, collect_date=collect_date)
        if item is None:
            skipped += 1
            continue
        items.append(item)
    if skipped:
        logger.warning(
            "source.map.skipped_without_id",
            source=source_id,
            skipped=skipped,
            external_id_expr=spec.external_id,
        )
    return items


__all__ = [
    "MappingError",
    "extract_items",
    "map_item",
    "map_payload",
    "parse_dt",
    "resolve_path",
]
