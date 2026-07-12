"""seed the daily_intel_v1 tiering prompt

Adds the prompt, and nothing else. It deliberately does NOT repoint any existing
module at it: switching a task that is running in production is the operator's
call, not a migration's (`megatron use-day-bundle --module X`).

Revision ID: 0008_daily_intel_prompt
Revises: 0007_source_registry
Create Date: 2026-07-12 00:00:00+00:00
"""

from __future__ import annotations



revision = "0008_daily_intel_prompt"
down_revision = "0007_source_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No-op. Prompt seeding moved out of migrations into the file-based profile
    # seeder (`megatron.profile.loader`, run at bootstrap, create-if-missing).
    # Existing installs already have the row; fresh installs get it from
    # config/prompts/. Kept as a revision so the migration chain is unbroken.
    pass


def downgrade() -> None:
    pass
