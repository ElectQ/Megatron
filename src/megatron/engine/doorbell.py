"""The push message (§3.5).

Split into two halves so the *content design* is not welded into the framework:

* The engine (here) prepares the data from the LLM-analysed bundle — which items
  are 必看 / 推荐, their clipped titles and labelled links, the stats, the day-page
  link — and owns the *mechanism*: the whole-item budget that trims 推荐 from the
  tail to fit a channel's char limit (never a mid-sentence cut, and it says
  "另有 N 条" when it does).
* The template (`config/digests/<style>.md`, a Jinja/markdown file) owns the
  *presentation*: labels, emojis, which sections appear, wording. Customising a
  notification is editing a file, not writing a render function.

`digest_style` on the task selects the template, exactly the way `page_layout`
selects the day-page template. `digest` is the tiered push (推特); `feed` is the
link-only push for page-only sources (GitHub 关注流).

No URL is ever printed raw: everything jumps through a label (原文 / 详情), because
a chat client cannot read a bare status URL and the day link carries a capability
token that has no business being visible in a group.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path

from .bundle import push_sections

# DingTalk's is the tightest limit of the shipped channels (~4500 chars, beyond
# which it splits into several messages). Stay under it, with room for the header
# and the footer.
MAX_DIGEST_CHARS = 3800

MAX_ONE_LINER = 80
MAX_WHY = 70

DEFAULT_STYLE = "digest"


def render_digest(bundle: dict, max_chars: int = MAX_DIGEST_CHARS, body: str | None = None) -> str:
    """Render the push for this bundle using its `digest_style` template.

    `body` is the template source, resolved by the caller (DB row → file). When
    omitted, the file for `digest_style` is used — the runner passes the DB body so
    admin-UI edits take effect; tests and ad-hoc callers get the file.

    The engine trims whole 推荐 items until the rendered message fits; 必看 and the
    day link are never trimmed.
    """
    style = bundle.get("digest_style") or DEFAULT_STYLE
    template = _compile(body) if body is not None else _load_template(style)

    must_see, recommend = push_sections(bundle)
    stats = bundle.get("stats") or {}
    base = {
        "title": bundle.get("title") or "情报日刊",
        "date": bundle.get("date", ""),
        "ingest_total": stats.get("ingest_total", 0),
        "stats": stats,
        "day_url": bundle.get("day_url") or "",
        "must_see": [_ctx_item(i) for i in must_see],
    }
    rec_ctx = [_ctx_item(i) for i in recommend]

    text = ""
    for trimmed in range(len(rec_ctx) + 1):
        ctx = {**base, "recommend": rec_ctx[: len(rec_ctx) - trimmed], "trimmed": trimmed}
        text = _tidy(template.render(**ctx))
        if len(text) <= max_chars:
            return text
    return text


def _ctx_item(item: dict) -> dict:
    """One item, reduced to what a template lays out: clipped title, why, link."""
    return {
        "title": _title(item),
        "why": _clip(item.get("why_for_me") or "", MAX_WHY),
        "url": item.get("url") or item.get("original_url") or "",
    }


def _title(item: dict) -> str:
    text = _clip(item.get("one_liner") or item.get("content") or "", MAX_ONE_LINER)
    return text.replace("[", "(").replace("]", ")")  # no bracket nesting next to links


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _tidy(text: str) -> str:
    """Collapse the blank-line runs Jinja block tags leave behind."""
    return re.sub(r"\n{3,}", "\n\n", text).strip()


@functools.lru_cache(maxsize=8)
def _env():
    from jinja2 import StrictUndefined
    from jinja2.sandbox import SandboxedEnvironment

    return SandboxedEnvironment(
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,  # markdown for a chat client, not HTML
        keep_trailing_newline=False,
    )


def _compile(body: str):
    return _env().from_string(body)


@functools.lru_cache(maxsize=16)
def _load_template(style: str):
    """Compile `config/digests/<style>.md` (falls back to the default style).

    The file fallback for callers that don't resolve from the DB (tests, previews).
    """
    from ..config import settings

    root = Path(settings.config_dir) / "digests"
    path = root / f"{style}.md"
    if not path.is_file():
        path = root / f"{DEFAULT_STYLE}.md"
    return _compile(path.read_text())


# The old names. It stopped being a doorbell when it grew a 推荐 section.
render_doorbell = render_digest
MAX_DOORBELL_CHARS = MAX_DIGEST_CHARS

__all__ = ["MAX_DIGEST_CHARS", "MAX_DOORBELL_CHARS", "render_digest", "render_doorbell"]
