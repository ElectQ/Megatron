"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-06-18 00:00:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("item_id", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.String(length=64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("author", sa.String(length=128), nullable=False),
        sa.Column("author_name", sa.String(length=256), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("collect_date", sa.String(length=16), nullable=False),
        sa.Column("is_retweet", sa.Boolean(), nullable=False),
        sa.Column("is_quote", sa.Boolean(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("links", sa.JSON(), nullable=False),
        sa.Column("media", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.Column("importance_score", sa.Float(), nullable=False),
        sa.Column("analysis_state", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_items_analysis_state", "items", ["analysis_state"])
    op.create_index("ix_items_author", "items", ["author"])
    op.create_index("ix_items_collect_date", "items", ["collect_date"])
    op.create_index("ix_items_item_id", "items", ["item_id"])
    op.create_index("ix_items_published_at", "items", ["published_at"])
    op.create_index("ix_items_source", "items", ["source"])
    op.create_index("ix_items_source_ref", "items", ["source_ref"])
    op.create_index("ux_items_unique", "items", ["source", "item_id"], unique=True)
    if op.get_bind().dialect.name == "sqlite":
        op.execute("CREATE INDEX ix_items_date ON items (date(published_at))")

    op.create_table(
        "ingest_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.String(length=64), nullable=False),
        sa.Column("date", sa.String(length=16), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("ingested", sa.Integer(), nullable=False),
        sa.Column("duplicated", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ingest_logs_source", "ingest_logs", ["source"])

    op.create_table(
        "pull_state",
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("last_date", sa.String(length=16), nullable=False),
        sa.Column("last_pull_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("source"),
    )

    op.create_table(
        "llm_providers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("api_base", sa.String(length=256), nullable=False),
        sa.Column("api_key", sa.Text(), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_providers_name", "llm_providers", ["name"], unique=True)

    op.create_table(
        "prompt_templates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("output_schema", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_prompt_templates_is_active", "prompt_templates", ["is_active"])
    op.create_index("ix_prompt_templates_name", "prompt_templates", ["name"])

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "webhook_channels",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_webhook_channels_kind", "webhook_channels", ["kind"])
    op.create_index("ix_webhook_channels_name", "webhook_channels", ["name"], unique=True)

    op.create_table(
        "analysis_modules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.String(length=64), nullable=False),
        sa.Column("filter_config", sa.JSON(), nullable=False),
        sa.Column("prompt_template_id", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=False),
        sa.Column("agent_backend", sa.String(length=32), nullable=False),
        sa.Column("tools_config", sa.JSON(), nullable=False),
        sa.Column("webhook_channel_ids", sa.JSON(), nullable=False),
        sa.Column("schedule_cron", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["prompt_template_id"], ["prompt_templates.id"]),
        sa.ForeignKeyConstraint(["provider_id"], ["llm_providers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analysis_modules_name", "analysis_modules", ["name"], unique=True)
    op.create_index("ix_analysis_modules_prompt_template_id", "analysis_modules", ["prompt_template_id"])
    op.create_index("ix_analysis_modules_provider_id", "analysis_modules", ["provider_id"])

    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("module_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("input_count", sa.Integer(), nullable=False),
        sa.Column("input_item_ids", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_cost_usd", sa.Float(), nullable=False),
        sa.Column("duration_sec", sa.Float(), nullable=False),
        sa.Column("tool_calls", sa.JSON(), nullable=False),
        sa.Column("triggered_by", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["module_id"], ["analysis_modules.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analysis_runs_module_id", "analysis_runs", ["module_id"])
    op.create_index("ix_analysis_runs_status", "analysis_runs", ["status"])

    op.create_table(
        "delivery_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("channel_name", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["channel_id"], ["webhook_channels.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_delivery_logs_channel_id", "delivery_logs", ["channel_id"])
    op.create_index("ix_delivery_logs_run_id", "delivery_logs", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_delivery_logs_run_id", table_name="delivery_logs")
    op.drop_index("ix_delivery_logs_channel_id", table_name="delivery_logs")
    op.drop_table("delivery_logs")
    op.drop_index("ix_analysis_runs_status", table_name="analysis_runs")
    op.drop_index("ix_analysis_runs_module_id", table_name="analysis_runs")
    op.drop_table("analysis_runs")
    op.drop_index("ix_analysis_modules_provider_id", table_name="analysis_modules")
    op.drop_index("ix_analysis_modules_prompt_template_id", table_name="analysis_modules")
    op.drop_index("ix_analysis_modules_name", table_name="analysis_modules")
    op.drop_table("analysis_modules")
    op.drop_index("ix_webhook_channels_name", table_name="webhook_channels")
    op.drop_index("ix_webhook_channels_kind", table_name="webhook_channels")
    op.drop_table("webhook_channels")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
    op.drop_index("ix_prompt_templates_name", table_name="prompt_templates")
    op.drop_index("ix_prompt_templates_is_active", table_name="prompt_templates")
    op.drop_table("prompt_templates")
    op.drop_index("ix_llm_providers_name", table_name="llm_providers")
    op.drop_table("llm_providers")
    op.drop_table("pull_state")
    op.drop_index("ix_ingest_logs_source", table_name="ingest_logs")
    op.drop_table("ingest_logs")
    if op.get_bind().dialect.name == "sqlite":
        op.drop_index("ix_items_date", table_name="items")
    op.drop_index("ux_items_unique", table_name="items")
    op.drop_index("ix_items_source_ref", table_name="items")
    op.drop_index("ix_items_source", table_name="items")
    op.drop_index("ix_items_published_at", table_name="items")
    op.drop_index("ix_items_item_id", table_name="items")
    op.drop_index("ix_items_collect_date", table_name="items")
    op.drop_index("ix_items_author", table_name="items")
    op.drop_index("ix_items_analysis_state", table_name="items")
    op.drop_table("items")
