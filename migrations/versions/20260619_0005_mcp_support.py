"""add mcp support

Revision ID: 0005_mcp_support
Revises: 20260618_0004_active_run_index
Create Date: 2026-06-19 00:00:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_mcp_support"
down_revision = "0004_active_run_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create mcp_servers table
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("server_url", sa.String(length=512), nullable=False),
        sa.Column("transport", sa.String(length=16), nullable=False, server_default="sse"),
        sa.Column("capabilities", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="disconnected"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mcp_servers_name", "mcp_servers", ["name"], unique=True)

    # Create source_configs table
    op.create_table(
        "source_configs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_source_configs_name", "source_configs", ["name"], unique=True)
    op.create_index("ix_source_configs_source_type", "source_configs", ["source_type"])


def downgrade() -> None:
    op.drop_index("ix_source_configs_source_type", table_name="source_configs")
    op.drop_index("ix_source_configs_name", table_name="source_configs")
    op.drop_table("source_configs")
    op.drop_index("ix_mcp_servers_name", table_name="mcp_servers")
    op.drop_table("mcp_servers")
