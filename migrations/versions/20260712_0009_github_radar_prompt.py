"""seed the github_radar_v1 tiering prompt

Adds the prompt only. The module that uses it (github_followee_briefing) is
created idempotently by bootstrap once an LLM provider exists — a migration that
ran before the provider was configured could not attach one, so module creation
lives where the provider is guaranteed.

Revision ID: 0009_github_radar_prompt
Revises: 0008_daily_intel_prompt
Create Date: 2026-07-12 00:00:00+00:00
"""

from __future__ import annotations



revision = "0009_github_radar_prompt"
down_revision = "0008_daily_intel_prompt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No-op. Prompt seeding moved out of migrations into the file-based profile
    # seeder (`megatron.profile.loader`, run at bootstrap, create-if-missing), so
    # this migration no longer imports prompt bodies from the framework. Existing
    # installs already have the row; fresh installs get it from config/prompts/.
    pass


def downgrade() -> None:
    pass
