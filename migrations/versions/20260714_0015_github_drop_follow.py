"""tell the github prompt to drop follow events

Follow events ("A followed B") now have their own home — the "新晋雷达" board on
the token-gated day page, built deterministically from the rows. They should not
also flow through the tiered digest: that would put the actor (a followee — your
follow graph) into the analysis bundle, and a follow has no repo to rank anyway.
So the prompt is told to `drop` them.

DB-is-truth prompt, so a file edit does nothing to a running instance — this
carries the one changed block. Surgical swap, no-op on a fresh DB.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_github_drop_follow"
down_revision = "0014_github_public_redacted"
branch_labels = None
depends_on = None

PROMPT = "github_radar_v1"
MARKER = "- `drop`"

NEW_DROP = """- `drop`         —— 两种情况：①真正的噪音（明显的 bot 行为、和安全/技术完全无关的仓库：
  壁纸、追番、刷分脚本）；②**follow / 关注类事件**（content 是「某人 followed 某人」、
  或 tags 含 `kind:follow`）—— 这类**一律 drop**，它们由日刊页的「新晋雷达」板块单独呈现，
  不进分级、不推送、不公开。除此之外拿不准一律放 `skim`，不要 drop —— 用户要求"全部展示"。"""


def _swap_block(template: str, marker: str, new: str) -> str | None:
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
    row = conn.execute(
        sa.text("SELECT id, template FROM prompt_templates WHERE name = :n"), {"n": PROMPT}
    ).fetchone()
    if row:
        swapped = _swap_block(row[1] or "", MARKER, NEW_DROP)
        if swapped:
            conn.execute(
                sa.text("UPDATE prompt_templates SET template = :t WHERE id = :i"),
                {"t": swapped, "i": row[0]},
            )


def downgrade() -> None:
    """No-op: reverting would let follow events back into the tiered digest."""
