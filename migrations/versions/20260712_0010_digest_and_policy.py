"""create digest_templates and policy tables

Webhook message templates and the global filtering policy become DB-backed so
they are editable in the admin UI. Rows are seeded from config/ files at bootstrap
(create-if-missing); this migration only creates the empty tables.

Revision ID: 0010_digest_and_policy
Revises: 0009_github_radar_prompt
Create Date: 2026-07-12 00:00:00+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_digest_and_policy"
down_revision = "0009_github_radar_prompt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "digest_templates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("style", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_digest_templates_style", "digest_templates", ["style"], unique=True)

    op.create_table(
        "policy",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("caps", sa.JSON(), nullable=True),
        sa.Column("politics_blocklist", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "system_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("base_url", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_index("ix_digest_templates_style", table_name="digest_templates")
    op.drop_table("digest_templates")
    op.drop_table("policy")
    op.drop_table("system_settings")
