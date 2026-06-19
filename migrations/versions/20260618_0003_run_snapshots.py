"""add analysis run snapshots

Revision ID: 0003_run_snapshots
Revises: 0002_module_channels
Create Date: 2026-06-18 01:00:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_run_snapshots"
down_revision = "0002_module_channels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "analysis_runs",
        sa.Column("module_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "analysis_runs",
        sa.Column("prompt_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "analysis_runs",
        sa.Column("provider_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "analysis_runs",
        sa.Column("rendered_prompt_hash", sa.String(length=64), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("analysis_runs", "rendered_prompt_hash")
    op.drop_column("analysis_runs", "provider_snapshot")
    op.drop_column("analysis_runs", "prompt_snapshot")
    op.drop_column("analysis_runs", "module_snapshot")
