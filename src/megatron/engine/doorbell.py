"""The push message (§3.5).

It carries 必看 and 推荐 — the day's verdict, with a way straight to each original
post. 速览 stays on the page: it is the long tail, and dumping it into a chat
window is the thing the day page exists to avoid.

No URL is ever printed. A column of
`https://x.com/cr3ghost/status/2075659362732048802` is unreadable in a chat client,
and the day link carries a capability token that has no business being visible in
a group chat. Everything jumps through a label — 原文 / 详情 — and every channel we
ship (DingTalk, Telegram, WeCom, Feishu) renders `[text](url)`.

The budget is a whole-item budget. If the message will not fit, 推荐 is trimmed
from the tail and the message *says so* — it never cuts mid-sentence, and nothing
is quietly lost, because the page still holds all of it.
"""

from __future__ import annotations

from .bundle import push_sections

# DingTalk's is the tightest limit of the shipped channels (~4500 chars, beyond
# which it splits into several messages). Stay under it, with room for the header
# and the footer.
MAX_DIGEST_CHARS = 3800

MAX_ONE_LINER = 80
MAX_WHY = 70


def render_digest(bundle: dict, max_chars: int = MAX_DIGEST_CHARS) -> str:
    date = bundle.get("date", "")
    title = bundle.get("title") or "情报日刊"
    stats = bundle.get("stats") or {}
    day = bundle.get("day_url") or ""

    must_see, recommend = push_sections(bundle)

    head = [
        f"⚡ {title} · {date}",
        f"入库 {stats.get('ingest_total', 0)} · 必看 {len(must_see)} · 推荐 {len(recommend)}",
    ]
    foot = ["——", f"[📖 查看今日详情 →]({day})"] if day else []

    def render(trimmed: int) -> str:
        lines = list(head)

        if must_see:
            lines += ["", "🔴 **必看**", ""]
            for n, item in enumerate(must_see, 1):
                lines.append(f"{n}. **{_title(item)}**")
                why = _clip(item.get("why_for_me") or "", MAX_WHY)
                if why:
                    lines.append(f"   {why}")
                lines.append(f"   {_origin(item)}")
        else:
            lines += ["", "今日无必看条目。"]

        shown = recommend[: len(recommend) - trimmed] if trimmed else recommend
        if shown:
            lines += ["", "🟡 **推荐**", ""]
            lines += [f"- {_title(item)} {_origin(item)}" for item in shown]
        if trimmed:
            lines.append(f"- …另有 {trimmed} 条，见详情")

        return "\n".join(lines + ([""] + foot if foot else [])).strip()

    # Drop whole 推荐 items until it fits. The header, 必看 and the day link are not
    # negotiable — if even those overflow, the channel's own splitter takes over.
    # With 必看 ≤ 8 and 推荐 ≤ 15 this should never trigger; it is the backstop for
    # a task that raises its caps.
    for trimmed in range(len(recommend) + 1):
        text = render(trimmed)
        if len(text) <= max_chars:
            return text
    return render(len(recommend))


def _title(item: dict) -> str:
    text = _clip(item.get("one_liner") or item.get("content") or "", MAX_ONE_LINER)
    return text.replace("[", "(").replace("]", ")")  # no bracket nesting next to links


def _origin(item: dict) -> str:
    """The jump to the post, behind a label — never the raw URL."""
    url = item.get("url") or item.get("original_url") or ""
    return f"[原文 ↗]({url})" if url else ""


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


# The old names. It stopped being a doorbell when it grew a 推荐 section.
render_doorbell = render_digest
MAX_DOORBELL_CHARS = MAX_DIGEST_CHARS

__all__ = ["MAX_DIGEST_CHARS", "MAX_DOORBELL_CHARS", "render_digest", "render_doorbell"]
