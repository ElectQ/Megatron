"""0018 — the existing Twitter task gets the adaptive batch size."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _alembic(db: Path, target: str) -> None:
    env = dict(os.environ)
    env.update({"MEGATRON_DATABASE_URL": f"sqlite+aiosqlite:///{db}"})
    subprocess.run(
        [str(ROOT / ".venv" / "bin" / "alembic"), "upgrade", target],
        cwd=ROOT,
        check=True,
        capture_output=True,
        env=env,
    )


def test_existing_twitter_task_gets_analysis_batch_size(tmp_path):
    db = tmp_path / "old.db"
    _alembic(db, "0017_github_tiered_push")
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO analysis_modules "
        "(id,name,description,source,source_ref,filter_config,prompt_template_id,provider_id,"
        "agent_backend,tools_config,webhook_channel_ids,schedule_cron,enabled,created_at) "
        "VALUES (1,'twitter_security_briefing','old','twitter_security_list','',?,1,1,"
        "'none','[]','[]','0 1 * * *',1,'2026-01-01')",
        (json.dumps({"time_mode": "today", "output_mode": "day_bundle"}),),
    )
    con.commit()
    con.close()

    _alembic(db, "head")

    con = sqlite3.connect(db)
    fc, _ = con.execute(
        "SELECT filter_config, id FROM analysis_modules WHERE name='twitter_security_briefing'"
    ).fetchone()
    con.close()

    fc = json.loads(fc)
    assert fc["analysis_batch_size"] == 25
    assert fc["time_mode"] == "today"  # untouched


def test_a_task_that_already_has_a_batch_size_is_left_alone(tmp_path):
    db = tmp_path / "old.db"
    _alembic(db, "0017_github_tiered_push")
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO analysis_modules "
        "(id,name,description,source,source_ref,filter_config,prompt_template_id,provider_id,"
        "agent_backend,tools_config,webhook_channel_ids,schedule_cron,enabled,created_at) "
        "VALUES (1,'twitter_security_briefing','old','twitter_security_list','',?,1,1,"
        "'none','[]','[]','0 1 * * *',1,'2026-01-01')",
        (json.dumps({"analysis_batch_size": 40}),),
    )
    con.commit()
    con.close()

    _alembic(db, "head")

    con = sqlite3.connect(db)
    fc = con.execute(
        "SELECT filter_config FROM analysis_modules WHERE name='twitter_security_briefing'"
    ).fetchone()[0]
    con.close()

    assert json.loads(fc)["analysis_batch_size"] == 40


def test_fresh_db_without_the_task_is_a_no_op(tmp_path):
    db = tmp_path / "fresh.db"
    _alembic(db, "0017_github_tiered_push")
    _alembic(db, "head")  # must not raise
