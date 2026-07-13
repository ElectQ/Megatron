"""publication_overrides: operator publish/unpublish decisions for the public blog

What reaches the public frontend is decided per item by the analysis (`public` in
the run's bundle). That is a model judgement, and it is sometimes wrong — but the
operator had no way to pull a day (or one item) down short of editing the DB.

The override lives in its own table rather than rewriting `analysis_runs.result`,
because the run is the record of what the model actually produced. Overwriting it
would destroy the evidence that the model keeps mis-marking — the very signal you
need to fix the prompt.

`item_id == ""` is a day-level override (source + date); otherwise it scopes to a
single bundle item. No row = no override = whatever the analysis decided.

Revision ID: 0011_publication_overrides
Revises: 0010_digest_and_policy
Create Date: 2026-07-13 00:00:00+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_publication_overrides"
down_revision = "0010_digest_and_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "publication_overrides",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_id", sa.String(length=32), nullable=False),
        sa.Column("date", sa.String(length=16), nullable=False),
        sa.Column("item_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("published", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_publication_overrides_source_id", "publication_overrides", ["source_id"]
    )
    op.create_index("ix_publication_overrides_date", "publication_overrides", ["date"])
    # One decision per (source, date, item) — upserts key off this.
    op.create_index(
        "ux_pub_override",
        "publication_overrides",
        ["source_id", "date", "item_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_pub_override", table_name="publication_overrides")
    op.drop_index("ix_publication_overrides_date", table_name="publication_overrides")
    op.drop_index("ix_publication_overrides_source_id", table_name="publication_overrides")
    op.drop_table("publication_overrides")
