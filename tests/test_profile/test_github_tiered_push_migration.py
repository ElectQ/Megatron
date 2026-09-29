"""0017 — existing GitHub rows get the tagged DingTalk push configuration."""

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


def test_existing_github_task_becomes_a_tagged_dingtalk_push(tmp_path):
    db = tmp_path / "old.db"
    _alembic(db, "0016_deepseek_model")
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO prompt_templates "
        "(id,name,display_name,version,template,output_schema,is_active,created_at) "
        "VALUES (1,'github_radar_v1','old',1,'old prompt','{}',1,'2026-01-01')"
    )
    con.execute(
        "INSERT INTO llm_providers "
        "(id,name,model,api_key,api_base,temperature,max_tokens,enabled,created_at) "
        "VALUES (1,'deepseek','m','','',0.3,1000,1,'2026-01-01')"
    )
    con.execute(
        "INSERT INTO webhook_channels (id,name,kind,config,enabled,created_at) "
        "VALUES (1,'旧钉钉通道名','dingtalk','{}',1,'2026-01-01')"
    )
    con.execute(
        "INSERT INTO analysis_modules "
        "(id,name,description,source,source_ref,filter_config,prompt_template_id,provider_id,"
        "agent_backend,tools_config,webhook_channel_ids,schedule_cron,enabled,created_at) "
        "VALUES (1,'github_followee_briefing','old','github_followee_feed','',?,1,1,"
        "'none','[]','[]','0 1 * * *',1,'2026-01-01')",
        (json.dumps({"time_mode": "today", "digest_style": "feed", "caps": {}}),),
    )
    con.commit()
    con.close()

    _alembic(db, "head")

    con = sqlite3.connect(db)
    prompt = con.execute(
        "SELECT template FROM prompt_templates WHERE name='github_radar_v1'"
    ).fetchone()[0]
    style, body = con.execute(
        "SELECT style, body FROM digest_templates WHERE style='github'"
    ).fetchone()
    fc, channels = con.execute(
        "SELECT filter_config, webhook_channel_ids FROM analysis_modules "
        "WHERE name='github_followee_briefing'"
    ).fetchone()
    edge = con.execute(
        "SELECT module_id, channel_id FROM module_channels WHERE module_id=1"
    ).fetchone()
    con.close()

    fc = json.loads(fc)
    assert "fixed topic vocabulary" not in prompt  # prompt is Chinese product copy
    assert "red_team" in prompt and "多人 star" in prompt
    assert style == "github" and "查看仓库" in body
    assert fc["time_mode"] == "today"
    assert fc["digest_style"] == "github"
    assert fc["caps"]["must_see_max"] == 8
    assert json.loads(channels) == [1]
    assert edge == (1, 1)
