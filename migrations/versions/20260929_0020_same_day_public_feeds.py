"""Analyze GitHub and rss-website using the latest same-day bundle.

The public pages were one day behind because their seeded tasks used
``previous_day``. The collectors publish a usable daily bundle before the 09:00
analysis window, so these two public feeds should analyze the current collect
 date just like the Twitter briefing.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0020_same_day_public_feeds"
down_revision = "0019_channel_delivery_and_rss_test"
branch_labels = None
depends_on = None

TASKS = ("github_followee_briefing", "rss-website-briefing")


def upgrade() -> None:
    conn = op.get_bind()
    for name in TASKS:
        row = conn.execute(
            sa.text("SELECT id, filter_config FROM analysis_modules WHERE name = :name"),
            {"name": name},
        ).fetchone()
        if not row:
            continue
        module_id, raw_fc = row
        fc = json.loads(raw_fc) if isinstance(raw_fc, str) else dict(raw_fc or {})
        fc["time_mode"] = "today"
        conn.execute(
            sa.text("UPDATE analysis_modules SET filter_config = :fc WHERE id = :id"),
            {
                "fc": json.dumps(fc, ensure_ascii=False, separators=(",", ":")),
                "id": module_id,
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    for name in TASKS:
        row = conn.execute(
            sa.text("SELECT id, filter_config FROM analysis_modules WHERE name = :name"),
            {"name": name},
        ).fetchone()
        if not row:
            continue
        module_id, raw_fc = row
        fc = json.loads(raw_fc) if isinstance(raw_fc, str) else dict(raw_fc or {})
        fc["time_mode"] = "previous_day"
        conn.execute(
            sa.text("UPDATE analysis_modules SET filter_config = :fc WHERE id = :id"),
            {
                "fc": json.dumps(fc, ensure_ascii=False, separators=(",", ":")),
                "id": module_id,
            },
        )
