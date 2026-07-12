"""seed the daily_intel_v1 tiering prompt

Adds the prompt, and nothing else. It deliberately does NOT repoint any existing
module at it: switching a task that is running in production is the operator's
call, not a migration's (`megatron use-day-bundle --module X`).

Revision ID: 0008_daily_intel_prompt
Revises: 0007_source_registry
Create Date: 2026-07-12 00:00:00+00:00
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0008_daily_intel_prompt"
down_revision = "0007_source_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from megatron.engine.builtin import (
        DAILY_INTEL_V1,
        DAILY_INTEL_V1_DISPLAY,
        DAILY_INTEL_V1_NAME,
        DAILY_INTEL_V1_SCHEMA,
    )

    op.execute(
        sa.text(
            """
            INSERT INTO prompt_templates
                (name, display_name, version, template, output_schema, is_active, created_at)
            SELECT :name, :display, 1, :template, :schema, 1, :now
            WHERE NOT EXISTS (SELECT 1 FROM prompt_templates WHERE name = :name)
            """
        ).bindparams(
            name=DAILY_INTEL_V1_NAME,
            display=DAILY_INTEL_V1_DISPLAY,
            template=DAILY_INTEL_V1,
            schema=json.dumps(DAILY_INTEL_V1_SCHEMA),
            now=datetime.now(timezone.utc),
        )
    )


def downgrade() -> None:
    op.execute("DELETE FROM prompt_templates WHERE name = 'daily_intel_v1'")
