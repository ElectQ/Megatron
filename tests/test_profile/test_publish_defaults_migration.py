"""The 0012 data migration, run against a database that holds the *old* rows.

Prompts, digest templates and tasks are seeded create-if-missing, so a redeploy
cannot fix them once they exist — this migration is the only thing that does. An
empty database would prove nothing here (0012 is a deliberate no-op on one), so
these tests stand up a real DB at 0011, write the pre-change rows into it, and
then run the actual `alembic upgrade head` a deploy runs.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# The `public` block exactly as it stood before the source-level publish policy:
# it tells the model to default to false, which is what keeps the blog empty.
OLD_PUBLIC_BLOCK = """- `public`：`true` / `false`（**默认 false**）。这条能不能放到**公开博客**上给陌生人看？
  - `true` 只给**客观、已公开**的信息：已披露的 CVE / 漏洞、公开发布的工具或文章、公开的安全事件、官方公告。
  - `false`（默认）给任何**涉及这个用户个人视角**的：内部/敏感、以及所有拿不准的。
  - 注意：`why_for_me` 是「为什么和你有关」，本身是私人的；公开时系统会自动去掉它，
    所以你标 `public: true` 只表示「这条**客观事实**可公开」，不代表连你的私人解读也公开。
  - 宁可漏标 public，不可误标 —— 拿不准就 `false`。"""

OLD_PROMPT = f"""你是"个人安全情报雷达"的分级引擎。今天是 {{{{ now }}}}。

## 每条必须回填的字段
- `actionability`：`none` / `read` / `watch` / `try`
- `scores`：`relevance`(0-3) `actionability`(0-3)
{OLD_PUBLIC_BLOCK}

## 输入
共 {{{{ item_count }}}} 条：
"""

OLD_FEED_BODY = """⚡ {{ title }} · {{ date }}
今日 {{ ingest_total }} 条动态已汇总,点开看谁在关注什么。
{% if day_url %}

[📖 查看今日详情 →]({{ day_url }})
{% endif %}
"""


def _alembic(db: Path, target: str) -> None:
    subprocess.run(
        [str(ROOT / ".venv" / "bin" / "alembic"), "upgrade", target],
        cwd=ROOT,
        check=True,
        capture_output=True,
        env={
            "PATH": "/usr/bin:/bin",
            "MEGATRON_DATABASE_URL": f"sqlite+aiosqlite:///{db}",
        },
    )


def _seed_old(db: Path) -> None:
    """Write the rows an already-running instance would have, pre-0012."""
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO prompt_templates "
        "(id, name, display_name, version, template, output_schema, is_active, created_at) "
        "VALUES (1, 'daily_intel_v1', 'd', 1, ?, '{}', 1, '2026-01-01')",
        (OLD_PROMPT,),
    )
    con.execute(
        "INSERT INTO digest_templates (id, style, display_name, body, is_active) "
        "VALUES (1, 'feed', 'feed', ?, 1)",
        (OLD_FEED_BODY,),
    )
    con.execute(
        "INSERT INTO llm_providers "
        "(id, name, model, api_base, api_key, temperature, max_tokens, enabled, created_at) "
        "VALUES (1, 'p', 'm', '', '', 0.3, 100, 1, '2026-01-01')"
    )
    empty = json.dumps({})
    for i, (name, cron) in enumerate(
        [("twitter_security_briefing", "0 9 * * *"), ("github_followee_briefing", "0 23 * * *")],
        start=1,
    ):
        con.execute(
            "INSERT INTO analysis_modules "
            "(id, name, description, source, source_ref, filter_config, prompt_template_id, "
            " provider_id, agent_backend, tools_config, webhook_channel_ids, schedule_cron, "
            " enabled, created_at) "
            "VALUES (?, ?, '', 's', '', ?, 1, 1, 'none', '[]', '[]', ?, 1, '2026-01-01')",
            (i, name, empty, cron),
        )
    con.commit()
    con.close()


@pytest.fixture
def migrated(tmp_path):
    """A DB carrying the old rows, brought up through 0012.

    Deliberately stops at 0012 rather than `head`: 0013 rewrites the same `public`
    block again, and this file is about what 0012 alone is responsible for. The
    old-DB → head path is covered in test_publish_the_take_migration.py.
    """
    db = tmp_path / "old.db"
    _alembic(db, "0011_publication_overrides")
    _seed_old(db)
    _alembic(db, "0012_publish_defaults")
    con = sqlite3.connect(db)
    yield con
    con.close()


def test_the_prompt_stops_telling_the_model_to_default_to_private(migrated):
    template = migrated.execute(
        "SELECT template FROM prompt_templates WHERE name = 'daily_intel_v1'"
    ).fetchone()[0]
    assert "**默认 false**" not in template
    assert "**通常不用填**" in template
    assert "只在一条确实敏感时" in template


def test_the_swap_leaves_the_rest_of_the_prompt_alone(migrated):
    """Surgical, not a blanket overwrite — an operator's other edits must survive."""
    template = migrated.execute(
        "SELECT template FROM prompt_templates WHERE name = 'daily_intel_v1'"
    ).fetchone()[0]
    assert "个人安全情报雷达" in template  # text above the block
    assert "`scores`：`relevance`(0-3)" in template  # the bullet right before it
    assert "## 输入" in template  # the section right after it
    assert "共 {{ item_count }} 条：" in template


def test_the_feed_push_links_the_public_page(migrated):
    body = migrated.execute("SELECT body FROM digest_templates WHERE style = 'feed'").fetchone()[0]
    assert "public_url or day_url" in body


def test_both_tasks_move_to_0900_beijing(migrated):
    rows = dict(migrated.execute("SELECT name, schedule_cron FROM analysis_modules").fetchall())
    assert rows == {
        "twitter_security_briefing": "0 1 * * *",
        "github_followee_briefing": "0 1 * * *",
    }


def test_it_is_a_no_op_on_a_fresh_database(tmp_path):
    """A new install has no rows yet; seeding loads the current files after this."""
    db = tmp_path / "fresh.db"
    _alembic(db, "head")  # must not raise
    con = sqlite3.connect(db)
    assert con.execute("SELECT count(*) FROM prompt_templates").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM digest_templates").fetchone()[0] == 0
    con.close()


def test_rerunning_the_swap_changes_nothing(migrated):
    """Idempotent: a second pass over an already-updated prompt is a no-op."""
    import importlib.util

    path = ROOT / "migrations" / "versions" / "20260713_0012_publish_defaults.py"
    spec = importlib.util.spec_from_file_location("m0012", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    template = migrated.execute(
        "SELECT template FROM prompt_templates WHERE name = 'daily_intel_v1'"
    ).fetchone()[0]
    assert mod._swap_public_block(template) is None
