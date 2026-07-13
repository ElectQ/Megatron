"""github feed goes public, with the follow graph redacted

The GitHub follow feed used to be hard-locked private, because each event —
"odzhan starred kernullist/KnWin32ApiMonitor" — is individually public yet the
day as a whole reveals *who you follow*. The value to a stranger, though, is
"what is the security scene starring lately", and that needs the repo and the
count, not the name. So the stream is published with the *who* redacted:

  - `source_configs.public_redact` (new column) — the public projection drops
    `author` and the raw `content` (which embeds the login) for a redacting
    source. Synced from the YAML like `audience`; the token-gated day page, which
    is the owner's own full-detail view, ignores it entirely.
  - `github_radar_v1` (DB-is-truth) — `one_liner`/`why_for_me` survive redaction,
    so the prompt is rewritten to forbid usernames in them (repo + count only),
    and `public` flips from "this never publishes" to "publishes, names removed
    for you".

The `audience: [public]` flip itself is in the source YAML and re-syncs on boot;
it needs no migration. This migration carries the column and the prompt, neither
of which the file-based seeding will touch on an already-running instance.
Surgical block swaps, like 0012/0013; a no-op on a fresh DB.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_github_public_redacted"
down_revision = "0013_publish_the_take"
branch_labels = None
depends_on = None

PROMPT = "github_radar_v1"

NEW_ONE_LINER = """- `one_liner`：**这个仓库是什么** + **有多少人在关注**。≤40 字。
  从 `owner/repo` 名字推断用途（安全圈仓库名通常很直白：`VeeamDumper-BOF`、`tgt-monitor-bof`），
  拿不准就照实说"看起来是…"，**不要编造功能**。例：`VeeamDumper-BOF：Veeam 凭据导出 BOF（3 人 star）`。
  **绝对不要出现任何 GitHub 用户名/关注者的名字** —— 这一行会公开给陌生人看,只说仓库和
  人数(「3 人 star」),永远不说是「谁」。谁关注的是这个用户的私事,不对外。"""

NEW_WHY = """- `why_for_me`：一句话说清**为什么这个仓库值得看**（≤35 字）。扣住意图或汇聚信号(N 人汇聚)。
  同样**不带任何人名** —— 这一行也会公开。"""

NEW_PUBLIC = """- `public`：**通常不用填**。这条流会上公开博客,但系统在公开时**自动隐去是谁 star 的**
  (author 和原始事件文本都会被剥离),只留你写的 `one_liner`/`why_for_me`。所以你只要
  保证那两行不带人名(见上),默认每条都可公开。
  - **只在仓库本身确实敏感时**才写 `public: false` —— 例如疑似恶意/钓鱼仓库、明显的私人
    项目。其余一律不填(等同公开)。"""

SWAPS = [
    ("- `one_liner`：", NEW_ONE_LINER),
    ("- `why_for_me`：", NEW_WHY),
    ("- `public`：", NEW_PUBLIC),
]


def _swap_block(template: str, marker: str, new: str) -> str | None:
    """Replace the bullet starting with `marker` (and its indented lines).

    The block ends at the next top-level bullet or `##` heading. None = no change.
    """
    lines = template.split("\n")
    start = next((i for i, ln in enumerate(lines) if ln.startswith(marker)), None)
    if start is None:
        return None
    end = start + 1
    while end < len(lines) and not (lines[end].startswith("## ") or lines[end].startswith("- `")):
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    swapped = "\n".join(lines[:start] + new.split("\n") + lines[end:])
    return None if swapped == template else swapped


def upgrade() -> None:
    conn = op.get_bind()

    # 1. The new column. server_default so existing rows are non-redacting; boot
    #    re-syncs each yaml-managed source to its declared value.
    op.add_column(
        "source_configs",
        sa.Column("public_redact", sa.Boolean(), nullable=False, server_default="0"),
    )
    # Set it now too, so the feed is redacted from the first request after this
    # migration even if the boot sync has not run yet. No-op on a fresh DB.
    conn.execute(
        sa.text("UPDATE source_configs SET public_redact = 1 WHERE name = 'github_followee_feed'")
    )
    conn.execute(
        sa.text("UPDATE source_configs SET audience = 'public' WHERE name = 'github_followee_feed'")
    )

    # 2. The prompt (DB is truth once seeded).
    row = conn.execute(
        sa.text("SELECT id, template FROM prompt_templates WHERE name = :n"), {"n": PROMPT}
    ).fetchone()
    if row:
        template = row[1] or ""
        for marker, new in SWAPS:
            swapped = _swap_block(template, marker, new)
            if swapped:
                template = swapped
        if template != (row[1] or ""):
            conn.execute(
                sa.text("UPDATE prompt_templates SET template = :t WHERE id = :i"),
                {"t": template, "i": row[0]},
            )


def downgrade() -> None:
    op.drop_column("source_configs", "public_redact")
