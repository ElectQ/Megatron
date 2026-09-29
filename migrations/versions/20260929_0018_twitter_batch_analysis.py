"""Batch long days so the model's answer never hits the completion limit.

daily_intel_v1 makes the model echo every input back as JSON; past ~80 items the
output array reaches the completion limit and the day's tail is lost — the
"LLM JSON truncated" warning on run #170. The runner now analyzes day_bundle
tasks in chunks (`filter_config.analysis_batch_size`), bisecting a still-
truncated chunk and degrading to skim so nothing is silently dropped. Existing
Twitter installs need the batch size seeded into their DB-is-truth task row.

Revision ID: 0018_twitter_batch_analysis
Revises: 0017_github_tiered_push
Create Date: 2026-09-29 00:00:00+00:00
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0018_twitter_batch_analysis"
down_revision = "0017_github_tiered_push"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    row = conn.execute(
        sa.text(
            "SELECT id, filter_config FROM analysis_modules "
            "WHERE name = 'twitter_security_briefing'"
        )
    ).fetchone()
    if not row:
        return
    fc = (
        json.loads(row.filter_config)
        if isinstance(row.filter_config, str)
        else dict(row.filter_config or {})
    )
    fc.setdefault("analysis_batch_size", 25)
    conn.execute(
        sa.text("UPDATE analysis_modules SET filter_config = :fc WHERE id = :id"),
        {"fc": json.dumps(fc), "id": row.id},
    )


def downgrade() -> None:
    pass
