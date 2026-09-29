"""Bind all production briefings to DingTalk + WeCom.

The profile files seed new installs, but existing AnalysisModule rows are DB-truth
and are not overwritten by normal seeding. Add the second production channel to
existing tasks here; the disabled rss-website test task is seeded from its file
on bootstrap and remains DingTalk-only.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0019_channel_delivery_and_rss_test"
down_revision = "0018_twitter_batch_analysis"
branch_labels = None
depends_on = None

TASKS = (
    "twitter_security_briefing",
    "github_followee_briefing",
    "rss-website-briefing",
)


def _channels(conn) -> list[int]:
    rows = conn.execute(
        sa.text(
            "SELECT id, kind FROM webhook_channels "
            "WHERE enabled = 1 AND kind IN ('dingtalk', 'wecom') ORDER BY id"
        )
    ).fetchall()
    by_kind: dict[str, int] = {}
    for channel_id, kind in rows:
        by_kind.setdefault(kind, channel_id)
    return [by_kind[kind] for kind in ("dingtalk", "wecom") if kind in by_kind]


def upgrade() -> None:
    conn = op.get_bind()
    channel_ids = _channels(conn)
    if not channel_ids:
        return

    for name in TASKS:
        row = conn.execute(
            sa.text("SELECT id, webhook_channel_ids FROM analysis_modules WHERE name = :name"),
            {"name": name},
        ).fetchone()
        if not row:
            continue

        module_id, raw_ids = row
        ids = json.loads(raw_ids) if isinstance(raw_ids, str) else list(raw_ids or [])
        ids = [int(value) for value in ids]
        for channel_id in channel_ids:
            if channel_id not in ids:
                ids.append(channel_id)

        conn.execute(
            sa.text(
                "UPDATE analysis_modules SET webhook_channel_ids = :ids WHERE id = :id"
            ),
            {"ids": json.dumps(ids), "id": module_id},
        )
        for position, channel_id in enumerate(ids):
            exists = conn.execute(
                sa.text(
                    "SELECT 1 FROM module_channels "
                    "WHERE module_id = :module_id AND channel_id = :channel_id"
                ),
                {"module_id": module_id, "channel_id": channel_id},
            ).fetchone()
            if not exists:
                conn.execute(
                    sa.text(
                        "INSERT INTO module_channels "
                        "(module_id, channel_id, position, created_at) "
                        "VALUES (:module_id, :channel_id, :position, CURRENT_TIMESTAMP)"
                    ),
                    {
                        "module_id": module_id,
                        "channel_id": channel_id,
                        "position": position,
                    },
                )


def downgrade() -> None:
    pass
