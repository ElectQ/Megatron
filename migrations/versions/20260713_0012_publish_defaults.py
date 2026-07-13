"""sync the three DB-is-truth rows the publish-policy change depends on

Prompts, digest templates and tasks are seeded *create-if-missing*: the file is
only a seed, and after first boot the DB row is the truth. That is the right
default (the operator edits them in the admin UI and a redeploy must not stomp
those edits), but it means three rows on an already-running instance are now
stale with respect to the source-level publish policy, and no amount of
redeploying will fix them by itself:

1. `daily_intel_v1` still tells the model `public` **defaults to false**. The new
   policy inverts that — inside a public source, absent means publish — so the
   old wording makes the model keep stamping `public: false` and the blog stays
   empty. This is the one that actually breaks the feature.
2. The `feed` digest template still links `day_url` (the token-gated personal
   page). It should prefer `public_url` — the shareable, token-free day page.
3. Both tasks still run on their old cron. Both should fire at 01:00 UTC
   (09:00 Beijing), which is also the only window in which the bundle's
   Beijing-labelled `collect_date` agrees with the UTC date the selector uses.

So this is a *data* migration, and it is deliberately surgical rather than a
blanket overwrite: it swaps the one `public` block inside the prompt (leaving
any other hand-editing intact) and leaves a `feed` body alone if it already
mentions `public_url`. On a fresh database it is a no-op — the rows do not exist
yet, and normal seeding loads the current files a moment later.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_publish_defaults"
down_revision = "0011_publication_overrides"
branch_labels = None
depends_on = None

# 09:00 Beijing. Also the safe window: upstream labels a bundle with the Beijing
# date, `_select_items` matches on the UTC date, and the two only agree between
# 00:00 and 15:59 UTC.
CRON = "0 1 * * *"
TASKS = ("twitter_security_briefing", "github_followee_briefing")

PROMPT = "daily_intel_v1"

# Replaces whatever `- `public`：...` block the stored prompt currently has.
NEW_PUBLIC_BLOCK = """- `public`：**通常不用填**。这条流是一份**公开安全日报**，默认每条都会上公开博客。
  - **只在一条确实敏感时**才显式写 `public: false` —— 例如：未公开披露的漏洞细节、
    内部/非公开渠道的信息、或明显涉及这个用户个人意图而非客观事实的内容。
  - 其余一律不填（等同公开）。不要因为「拿不准值不值得发」就压下 —— 值不值得发是
    `tier` 的事，`public` 只管**能不能公开**。
  - 你不需要担心泄露私人解读：`why_for_me` 和 `scores` 在公开时**由系统自动剥离**，
    博客上只会出现客观事实。所以「这条推文本来就是公开的」= 可以公开。"""

FEED_STYLE = "feed"
NEW_FEED_BODY = """{#-
  推送文案模板 —— 「仅链接」形态（GitHub 关注流等页面型源）。
  不铺条目,只发一个入口:标题 + 一行统计 + 日刊链接。详情都在页面里。
  可用变量:title / date / ingest_total / day_url / public_url / must_see / recommend / trimmed。
  这里用前四个 + public_url。

  链接优先用 public_url —— 公开前台的当日页(无 token、可分享给钉钉群里所有人、可被搜索引擎收录);
  当天没有可公开内容时 public_url 为空,自动回落到私有的 day_url(带不可猜 token 的个人页)。
-#}
⚡ {{ title }} · {{ date }}
今日 {{ ingest_total }} 条动态已汇总,点开看谁在关注什么。
{% set link = public_url or day_url %}
{% if link %}

[📖 查看今日详情 →]({{ link }})
{% endif %}
"""


def _swap_public_block(template: str) -> str | None:
    """Replace the prompt's `public` field block. None = nothing to swap.

    The block runs from the `- `public`：` bullet to the next top-level bullet or
    section heading, so it survives the prompt having been reworded around it.
    """
    lines = template.split("\n")
    start = next((i for i, ln in enumerate(lines) if ln.startswith("- `public`：")), None)
    if start is None:
        return None

    end = start + 1
    while end < len(lines) and not (lines[end].startswith("## ") or lines[end].startswith("- `")):
        end += 1
    # Keep the blank line(s) that separate the block from whatever follows.
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1

    new = lines[:start] + NEW_PUBLIC_BLOCK.split("\n") + lines[end:]
    swapped = "\n".join(new)
    return None if swapped == template else swapped


def upgrade() -> None:
    conn = op.get_bind()

    row = conn.execute(
        sa.text("SELECT id, template FROM prompt_templates WHERE name = :n"),
        {"n": PROMPT},
    ).fetchone()
    if row:
        swapped = _swap_public_block(row[1] or "")
        if swapped:
            conn.execute(
                sa.text("UPDATE prompt_templates SET template = :t WHERE id = :i"),
                {"t": swapped, "i": row[0]},
            )

    conn.execute(
        sa.text(
            "UPDATE digest_templates SET body = :b "
            "WHERE style = :s AND body NOT LIKE '%public_url%'"
        ),
        {"b": NEW_FEED_BODY, "s": FEED_STYLE},
    )

    for name in TASKS:
        conn.execute(
            sa.text(
                "UPDATE analysis_modules SET schedule_cron = :c "
                "WHERE name = :n AND schedule_cron != :c"
            ),
            {"c": CRON, "n": name},
        )


def downgrade() -> None:
    """No-op: the previous prompt/template wording is not worth reconstructing,
    and reverting the cron would only restore a schedule that mislabels the day."""
