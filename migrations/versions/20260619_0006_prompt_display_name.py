"""add display_name to prompt_templates

Revision ID: 0006_prompt_display_name
Revises: 0005_mcp_support
Create Date: 2026-06-19 00:00:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_prompt_display_name"
down_revision = "0005_mcp_support"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add display_name column
    op.add_column(
        "prompt_templates",
        sa.Column("display_name", sa.String(length=128), nullable=False, server_default=""),
    )
    
    # Update existing data
    op.execute("UPDATE prompt_templates SET display_name = '推特安全信息流简报' WHERE name = 'daily_security_briefing'")


def downgrade() -> None:
    op.drop_column("prompt_templates", "display_name")
