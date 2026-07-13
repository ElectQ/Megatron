"""publish the model's take — and tell the model that it is published

The public blog was rendering a headline plus the raw tweet, and nothing else:
the model's reasoning (`why_for_me`) was stripped on the way out, which made the
blog a bare mirror of other people's posts. It is now published.

That change is only safe if the prompt moves with it. The old prompt promised the
model that `why_for_me` "会由系统自动剥离" — under that promise it writes lines
addressed to the reader ("你在跑自建 Samba，正好中招"), which is exactly the
intent leak the source-level gate exists to prevent. Publishing text written under
a promise of privacy is worse than not publishing it at all, so the two have to
land together:

  - `why_for_me` is now specified as an *objective* reason to read (影响面 /
    可利用性), explicitly "会原样出现在公开博客上", with 「你」 ruled out.
  - the `public` block drops the stripping promise and tells the model to judge
    one_liner + why_for_me together when deciding whether an item may be public.
  - `github_radar_v1` stops asking for a `public` call at all: that source is
    hard-locked private at the source level, so the field cannot change anything.

Prompts are DB-is-truth (seeded create-if-missing), so editing the files does
nothing to a running instance — hence this. Surgical, like 0012: it swaps only
the named blocks, so any other hand-editing survives. No-op on a fresh database.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_publish_the_take"
down_revision = "0012_publish_defaults"
branch_labels = None
depends_on = None

DAILY_WHY = """- `why_for_me`：一句话说清**为什么这条值得读**（≤35 字）—— 影响面、可利用性、
  或它相对同类的增量。**这一行会原样出现在公开博客上**，是读者看到的唯一解读。
  所以：用上面的意图来**筛选**，但**写成客观陈述，不要出现「你」「你的」**。
  ✅「默认配置即受影响，PoC 已公开，补丁未覆盖 LTS。」
  ❌「你在跑自建 Samba，正好中招。」
  不能写成泛泛的"值得关注"。写不出具体理由的，说明它不该是高档位。"""

DAILY_PUBLIC = """- `public`：**通常不用填**。这条流是一份**公开安全日报**，默认每条都会上公开博客。
  - **只在一条确实敏感时**才显式写 `public: false` —— 例如：未公开披露的漏洞细节、
    内部/非公开渠道的信息、或明显涉及这个用户个人意图而非客观事实的内容。
  - 其余一律不填（等同公开）。不要因为「拿不准值不值得发」就压下 —— 值不值得发是
    `tier` 的事，`public` 只管**能不能公开**。
  - 注意：`scores` 不会公开，但 `one_liner` 和 `why_for_me` **会**。所以判断"能不能公开"时，
    连同你为它写的那两行一起判断 —— 只要它们都是客观陈述，「这条推文本来就是公开的」= 可以公开。"""

RADAR_PUBLIC = """- `public`：**这条流不上公开博客，填什么都不会改变这一点** —— 它在源这一级就被锁成私有了
  （公开它等于公开「这个用户关注了谁」，泄露的是关注图谱而非单条内容）。
  所以不用纠结，留空即可。"""

# (prompt name, bullet marker, replacement block)
SWAPS = [
    ("daily_intel_v1", "- `why_for_me`：", DAILY_WHY),
    ("daily_intel_v1", "- `public`：", DAILY_PUBLIC),
    ("github_radar_v1", "- `public`：", RADAR_PUBLIC),
]


def _swap_block(template: str, marker: str, new: str) -> str | None:
    """Replace the bullet starting with `marker` (and its indented lines).

    The block ends at the next top-level bullet or `##` heading, so it survives
    the prompt having been reworded around it. None = nothing to change.
    """
    lines = template.split("\n")
    start = next((i for i, ln in enumerate(lines) if ln.startswith(marker)), None)
    if start is None:
        return None

    end = start + 1
    while end < len(lines) and not (lines[end].startswith("## ") or lines[end].startswith("- `")):
        end += 1
    # Keep the blank line(s) separating the block from whatever follows.
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1

    swapped = "\n".join(lines[:start] + new.split("\n") + lines[end:])
    return None if swapped == template else swapped


def upgrade() -> None:
    conn = op.get_bind()
    for name, marker, new in SWAPS:
        row = conn.execute(
            sa.text("SELECT id, template FROM prompt_templates WHERE name = :n"),
            {"n": name},
        ).fetchone()
        if not row:
            continue
        swapped = _swap_block(row[1] or "", marker, new)
        if swapped:
            conn.execute(
                sa.text("UPDATE prompt_templates SET template = :t WHERE id = :i"),
                {"t": swapped, "i": row[0]},
            )


def downgrade() -> None:
    """No-op. Reverting the prompt without reverting `_STRIP` would leave the
    model writing "你…" lines straight onto the public blog — the one state this
    migration exists to make impossible."""
