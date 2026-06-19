"""add module channel association table

Revision ID: 0002_module_channels
Revises: 0001_initial_schema
Create Date: 2026-06-18 00:30:00+00:00
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "0002_module_channels"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "module_channels",
        sa.Column("module_id", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["channel_id"], ["webhook_channels.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["module_id"], ["analysis_modules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("module_id", "channel_id"),
    )
    op.create_index("ix_module_channels_channel_id", "module_channels", ["channel_id"])
    op.create_index("ix_module_channels_module_id", "module_channels", ["module_id"])

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, webhook_channel_ids FROM analysis_modules")).fetchall()
    existing_channels = {
        int(row[0]) for row in conn.execute(sa.text("SELECT id FROM webhook_channels")).fetchall()
    }
    for module_id, raw_ids in rows:
        ids = [channel_id for channel_id in _parse_channel_ids(raw_ids) if channel_id in existing_channels]
        for pos, channel_id in enumerate(ids):
            conn.execute(
                sa.text(
                    "INSERT INTO module_channels "
                    "(module_id, channel_id, position, created_at) "
                    "VALUES (:module_id, :channel_id, :position, CURRENT_TIMESTAMP)"
                ),
                {"module_id": module_id, "channel_id": channel_id, "position": pos},
            )


def downgrade() -> None:
    op.drop_index("ix_module_channels_module_id", table_name="module_channels")
    op.drop_index("ix_module_channels_channel_id", table_name="module_channels")
    op.drop_table("module_channels")


def _parse_channel_ids(raw) -> list[int]:
    if not raw:
        return []
    if isinstance(raw, list):
        values = raw
    else:
        try:
            values = json.loads(raw)
        except Exception:
            return []
    out: list[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            channel_id = int(value)
        except (TypeError, ValueError):
            continue
        if channel_id not in seen:
            seen.add(channel_id)
            out.append(channel_id)
    return out
