"""turn the GitHub daily link into a tagged must-see/recommend push

Prompt, digest and task rows are DB-is-truth after their first seed, so changing
config files alone does not update an existing install. This migration updates
only the GitHub-owned rows and binds the existing DingTalk channel when present.

Revision ID: 0017_github_tiered_push
Revises: 0016_deepseek_model
Create Date: 2026-09-28 00:00:00+00:00
"""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0017_github_tiered_push"
down_revision = "0016_deepseek_model"
branch_labels = None
depends_on = None

ROOT = Path(__file__).resolve().parents[2]


def _text(path: str) -> str:
    return (ROOT / path).read_text()


def _prompt_body() -> str:
    text = _text("config/prompts/github_radar_v1.md")
    _, _, rest = text.partition("---\n")
    _, _, body = rest.partition("\n---")
    return body.lstrip("\n")


def upgrade() -> None:
    conn = op.get_bind()

    prompt = conn.execute(
        sa.text("SELECT id FROM prompt_templates WHERE name = 'github_radar_v1'")
    ).fetchone()
    row = conn.execute(
        sa.text("SELECT id, filter_config, webhook_channel_ids FROM analysis_modules "
                "WHERE name = 'github_followee_briefing'")
    ).fetchone()

    # Fresh databases have neither row yet; bootstrap seeds the current files
    # immediately after Alembic. Existing installs need their DB-is-truth rows
    # updated here because normal seeding deliberately never overwrites them.
    if not prompt and not row:
        return

    if prompt:
        conn.execute(
            sa.text("UPDATE prompt_templates SET template = :body, display_name = :name "
                    "WHERE id = :id"),
            {
                "body": _prompt_body(),
                "name": "GitHub 关注流分级（必看/推荐推送）",
                "id": prompt[0],
            },
        )

    digest = conn.execute(
        sa.text("SELECT id FROM digest_templates WHERE style = 'github'")
    ).fetchone()
    body = _text("config/digests/github.md")
    if digest:
        conn.execute(
            sa.text("UPDATE digest_templates SET body = :body, display_name = :name "
                    "WHERE id = :id"),
            {"body": body, "name": "GitHub 分档推送", "id": digest[0]},
        )
    else:
        conn.execute(
            sa.text("INSERT INTO digest_templates "
                    "(style, display_name, body, is_active, created_at, updated_at) "
                    "VALUES ('github', :name, :body, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
            {"name": "GitHub 分档推送", "body": body},
        )

    if not row:
        return

    module_id, raw_fc, raw_channels = row
    fc = json.loads(raw_fc) if isinstance(raw_fc, str) else dict(raw_fc or {})
    fc.update({"time_mode": "previous_day", "digest_style": "github"})
    caps = dict(fc.get("caps") or {})
    caps.update({"lead_min": 0, "must_see_min": 0, "must_see_max": 8, "recommend_max": 15})
    fc["caps"] = caps

    channel = conn.execute(
        sa.text("SELECT id FROM webhook_channels "
                "WHERE kind = 'dingtalk' AND enabled = 1 "
                "ORDER BY CASE WHEN name = '钉钉安全简报' THEN 0 ELSE 1 END, id LIMIT 1")
    ).fetchone()
    channels = json.loads(raw_channels) if isinstance(raw_channels, str) else list(raw_channels or [])
    if channel and channel[0] not in channels:
        channels.append(channel[0])

    conn.execute(
        sa.text("UPDATE analysis_modules SET description = :description, filter_config = :fc, "
                "webhook_channel_ids = :channels WHERE id = :id"),
        {
            "description": "每日 GitHub 关注流分级（必看/推荐推送 + 日刊页）",
            "fc": json.dumps(fc, ensure_ascii=False, separators=(",", ":")),
            "channels": json.dumps(channels),
            "id": module_id,
        },
    )

    if channel:
        exists = conn.execute(
            sa.text("SELECT 1 FROM module_channels WHERE module_id = :m AND channel_id = :c"),
            {"m": module_id, "c": channel[0]},
        ).fetchone()
        if not exists:
            position = conn.execute(
                sa.text("SELECT COUNT(*) FROM module_channels WHERE module_id = :m"),
                {"m": module_id},
            ).scalar_one()
            conn.execute(
                sa.text("INSERT INTO module_channels "
                        "(module_id, channel_id, position, created_at) "
                        "VALUES (:m, :c, :p, CURRENT_TIMESTAMP)"),
                {"m": module_id, "c": channel[0], "p": position},
            )


def downgrade() -> None:
    """Keep the safer previous-day selection; only restore the link-only style."""
    conn = op.get_bind()
    row = conn.execute(
        sa.text("SELECT id, filter_config FROM analysis_modules "
                "WHERE name = 'github_followee_briefing'")
    ).fetchone()
    if row:
        fc = json.loads(row[1]) if isinstance(row[1], str) else dict(row[1] or {})
        fc["digest_style"] = "feed"
        conn.execute(
            sa.text("UPDATE analysis_modules SET filter_config = :fc WHERE id = :id"),
            {"fc": json.dumps(fc, ensure_ascii=False, separators=(",", ":")), "id": row[0]},
        )
