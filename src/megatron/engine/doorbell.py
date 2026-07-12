"""The doorbell: the thin push (§3.5).

A notification is an interruption. Its job is to say *whether* you should stop
what you are doing, and nothing more — the reading happens on the day page. So
this renders at most `must_see_push_max` items and stays under ~1200 characters;
everything else in the day is one link away.

This is what replaced dumping twenty full summaries into a chat webhook.
"""

from __future__ import annotations

from .bundle import push_items

MAX_DOORBELL_CHARS = 1200

_TIER_ICON = "🔴"


def render_doorbell(bundle: dict) -> str:
    date = bundle.get("date", "")
    stats = bundle.get("stats") or {}
    ingest_total = stats.get("ingest_total", 0)
    url = bundle.get("day_url") or ""

    items = push_items(bundle)
    n = len(items)

    lines = [f"⚡ 安全雷达 · {date}"]
    header = f"入库 {ingest_total} · 强推 {n}"
    lines.append(header)
    lines.append("")

    if not items:
        lines.append("今日无必看条目。")
    else:
        for i, item in enumerate(items, 1):
            one_liner = _clip(item.get("one_liner") or item.get("content") or "", 90)
            lines.append(f"{_TIER_ICON} {i}/{n}  {one_liner}")
            why = _clip(item.get("why_for_me") or "", 80)
            if why:
                lines.append(why)
            link = item.get("url") or item.get("original_url") or ""
            if link:
                lines.append(link)
            lines.append("")

    if url:
        lines.append("——")
        lines.append(f"全日刊：{url}")

    text = "\n".join(lines).strip()
    if len(text) > MAX_DOORBELL_CHARS:
        text = text[: MAX_DOORBELL_CHARS - 1].rstrip() + "…"
    return text


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


__all__ = ["MAX_DOORBELL_CHARS", "render_doorbell"]
