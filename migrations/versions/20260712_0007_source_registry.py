"""source registry: adapter/audience/schedule_expect + canonical source_id

Turns `source_configs` into the spec's Source registry (§3.1) and renames the
de-facto twitter source to its canonical `source_id`.

Why rewrite `items.source` instead of keeping an alias mapping:
`items.source` is one half of the dedup key `ux_items_unique(source, item_id)`.
Leaving the old label in place would split the dedup domain — once a collector
starts pushing `source_id=twitter_security_list`, every already-stored tweet
would be inserted a second time under the new label, and `_select_items` would
only ever see one of the two copies.

Revision ID: 0007_source_registry
Revises: 0006_prompt_display_name
Create Date: 2026-07-12 00:00:00+00:00
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0007_source_registry"
down_revision = "0006_prompt_display_name"
branch_labels = None
depends_on = None


CANONICAL = "twitter_security_list"
LEGACY = ("soundwave", "twitter")


def upgrade() -> None:
    # 1. Registry columns.
    op.add_column(
        "source_configs",
        sa.Column("display_name", sa.String(length=128), nullable=False, server_default=""),
    )
    op.add_column(
        "source_configs",
        sa.Column("kind", sa.String(length=32), nullable=False, server_default=""),
    )
    op.add_column(
        "source_configs",
        sa.Column("adapter", sa.String(length=16), nullable=False, server_default="native"),
    )
    op.add_column(
        "source_configs",
        sa.Column("audience", sa.String(length=16), nullable=False, server_default="personal"),
    )
    op.add_column(
        "source_configs",
        sa.Column("schedule_expect", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "source_configs",
        sa.Column("managed_by", sa.String(length=16), nullable=False, server_default="db"),
    )
    op.create_index("ix_source_configs_adapter", "source_configs", ["adapter"])
    op.create_index("ix_source_configs_managed_by", "source_configs", ["managed_by"])

    # Existing MCP rows become the degraded query adapter.
    op.execute("UPDATE source_configs SET adapter = 'mcp_query' WHERE source_type = 'mcp'")

    # 2. Seed the canonical source. Idempotent so a fresh install and an upgraded
    #    one converge on the same row. The YAML loader will reconcile it on boot.
    config = json.dumps(
        {
            "external_repo": "ElectQ/Soundwave",
            "legacy_aliases": list(LEGACY),
            # Transitional: lets the runner backfill from MCP on a day with zero
            # rows, until the collector starts pushing. See MEGATRON_MCP_LIVE_FETCH.
            "fallback_mcp": True,
        }
    )
    schedule = json.dumps(
        {"timezone": "Asia/Shanghai", "collect_by": "06:00", "sla_minutes": 90}
    )
    op.execute(
        sa.text(
            """
            INSERT INTO source_configs
                (name, display_name, kind, source_type, adapter, audience,
                 config, schedule_expect, managed_by, enabled, created_at)
            SELECT :name, :display, :kind, 'native', 'http_push', 'personal',
                   :config, :schedule, 'yaml', 1, :now
            WHERE NOT EXISTS (SELECT 1 FROM source_configs WHERE name = :name)
            """
        ).bindparams(
            name=CANONICAL,
            display="Twitter 安全 List",
            kind="twitter_list",
            config=config,
            schedule=schedule,
            now=datetime.now(timezone.utc),
        )
    )

    # 3. Rewrite the legacy source labels onto the canonical source_id.
    #    Delete would-be duplicates first: the unique index (source, item_id)
    #    would otherwise abort the UPDATE.
    legacy_list = ", ".join(f"'{s}'" for s in LEGACY)
    op.execute(
        f"""
        DELETE FROM items
         WHERE source IN ({legacy_list})
           AND item_id IN (SELECT item_id FROM items WHERE source = '{CANONICAL}')
        """
    )
    # 'twitter' and 'soundwave' can hold the same tweet; keep the 'soundwave' copy.
    op.execute(
        """
        DELETE FROM items
         WHERE source = 'twitter'
           AND item_id IN (SELECT item_id FROM items WHERE source = 'soundwave')
        """
    )
    op.execute(f"UPDATE items SET source = '{CANONICAL}' WHERE source IN ({legacy_list})")
    op.execute(f"UPDATE ingest_logs SET source = '{CANONICAL}' WHERE source IN ({legacy_list})")
    op.execute(
        f"UPDATE analysis_modules SET source = '{CANONICAL}' WHERE source IN ({legacy_list})"
    )

    # pull_state is keyed by source: collapse the legacy rows into one, keeping
    # the furthest watermark so we do not re-pull days we already have.
    # HAVING COUNT(*) > 0 is load-bearing: an aggregate with no GROUP BY still
    # yields one all-NULL row over an empty set, which would insert a NULL
    # last_date and trip its NOT NULL constraint.
    op.execute(
        f"""
        INSERT INTO pull_state (source, last_date, last_pull_at, updated_at)
        SELECT '{CANONICAL}', MAX(last_date), MAX(last_pull_at), MAX(updated_at)
          FROM pull_state
         WHERE source IN ({legacy_list})
           AND NOT EXISTS (SELECT 1 FROM pull_state WHERE source = '{CANONICAL}')
        HAVING COUNT(*) > 0
        """
    )
    op.execute(f"DELETE FROM pull_state WHERE source IN ({legacy_list})")

    # analysis_runs.module_snapshot is deliberately left alone: it is a historical
    # record of what the module looked like at the time, not live configuration.


def downgrade() -> None:
    op.execute(f"UPDATE items SET source = 'soundwave' WHERE source = '{CANONICAL}'")
    op.execute(f"UPDATE ingest_logs SET source = 'soundwave' WHERE source = '{CANONICAL}'")
    op.execute(f"UPDATE analysis_modules SET source = 'soundwave' WHERE source = '{CANONICAL}'")
    op.execute(f"UPDATE pull_state SET source = 'soundwave' WHERE source = '{CANONICAL}'")
    op.execute(f"DELETE FROM source_configs WHERE name = '{CANONICAL}'")

    op.drop_index("ix_source_configs_managed_by", table_name="source_configs")
    op.drop_index("ix_source_configs_adapter", table_name="source_configs")
    for col in ("managed_by", "schedule_expect", "audience", "adapter", "kind", "display_name"):
        op.drop_column("source_configs", col)
