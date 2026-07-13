"""0014 — the GitHub feed goes public with the follow graph redacted.

Two things a redeploy alone cannot do to an already-running instance: add the
`public_redact` column and set it for the feed, and rewrite the DB-is-truth
prompt so `one_liner`/`why_for_me` (which survive redaction) stop being allowed
to name a person. Both are run against a DB carrying the *old* state.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

OLD_RADAR = """## 字段
- `one_liner`：**这个仓库是什么** + 谁在关注。≤40 字。例：`X-BOF：用途（3 人 star）`。
- `why_for_me`：一句话说清**为什么值得这个用户看**（≤35 字）。扣住意图或汇聚信号。
- `topics`：2-4 个标签。
- `public`：**这条流不上公开博客，填什么都不会改变这一点** —— 它在源这一级就被锁成私有了。
  所以不用纠结，留空即可。

## 输入
"""


def _alembic(db: Path, target: str) -> None:
    subprocess.run(
        [str(ROOT / ".venv" / "bin" / "alembic"), "upgrade", target],
        cwd=ROOT,
        check=True,
        capture_output=True,
        env={"PATH": "/usr/bin:/bin", "MEGATRON_DATABASE_URL": f"sqlite+aiosqlite:///{db}"},
    )


@pytest.fixture
def migrated(tmp_path):
    """A DB with the pre-0014 github source (personal, no redact column) and the
    old prompt, brought to head."""
    db = tmp_path / "old.db"
    _alembic(db, "0013_publish_the_take")

    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO source_configs "
        "(name, source_type, adapter, audience, kind, managed_by, enabled, created_at) "
        "VALUES ('github_followee_feed','bundle_pull','bundle_pull','personal','github_feed',"
        "'yaml',1,'2026-01-01')"
    )
    con.execute(
        "INSERT INTO prompt_templates "
        "(id, name, display_name, version, template, output_schema, is_active, created_at) "
        "VALUES (1, 'github_radar_v1', '', 1, ?, '{}', 1, '2026-01-01')",
        (OLD_RADAR,),
    )
    con.commit()
    con.close()

    _alembic(db, "head")
    return db


def test_the_feed_becomes_public_and_redacting(migrated):
    con = sqlite3.connect(migrated)
    row = con.execute(
        "SELECT audience, public_redact FROM source_configs WHERE name = 'github_followee_feed'"
    ).fetchone()
    con.close()
    assert row == ("public", 1)


def test_the_prompt_forbids_names_in_the_lines_that_survive_redaction(migrated):
    con = sqlite3.connect(migrated)
    template = con.execute(
        "SELECT template FROM prompt_templates WHERE name = 'github_radar_v1'"
    ).fetchone()[0]
    con.close()
    assert "绝对不要出现任何 GitHub 用户名" in template
    assert "谁在关注" not in template  # the old wording that invited a name
    assert "这条流不上公开博客" not in template  # it does now


def test_a_fresh_database_gets_the_column_defaulting_to_not_redacting(tmp_path):
    """The schema migration runs on a fresh DB; the data updates are simply no-ops
    (no github row to touch), and the column defaults to 0 for everything else."""
    db = tmp_path / "fresh.db"
    _alembic(db, "head")
    con = sqlite3.connect(db)
    redacting = con.execute(
        "SELECT count(*) FROM source_configs WHERE public_redact = 1"
    ).fetchone()
    con.close()
    assert redacting[0] == 0
