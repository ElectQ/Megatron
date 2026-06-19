"""add active run lookup index

Revision ID: 0004_active_run_index
Revises: 0003_run_snapshots
Create Date: 2026-06-18 01:30:00+00:00
"""

from __future__ import annotations

from alembic import op


revision = "0004_active_run_index"
down_revision = "0003_run_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_analysis_runs_module_status", "analysis_runs", ["module_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_analysis_runs_module_status", table_name="analysis_runs")
