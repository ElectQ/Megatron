"""0015 — the github prompt learns to drop follow events."""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

OLD = """## 分级
- `must_see_page` —— 高价值仓库。
- `skim`         —— 其余。
- `drop`         —— **只给真正的噪音**：明显的 bot 行为、和安全/技术完全无关的仓库。
  拿不准一律放 `skim`，不要 drop。

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


def test_the_prompt_now_drops_follow_events(tmp_path):
    db = tmp_path / "old.db"
    _alembic(db, "0014_github_public_redacted")
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO prompt_templates "
        "(id, name, display_name, version, template, output_schema, is_active, created_at) "
        "VALUES (1, 'github_radar_v1', '', 1, ?, '{}', 1, '2026-01-01')",
        (OLD,),
    )
    con.commit()
    con.close()

    _alembic(db, "head")

    con = sqlite3.connect(db)
    template = con.execute(
        "SELECT template FROM prompt_templates WHERE name = 'github_radar_v1'"
    ).fetchone()[0]
    con.close()
    assert "follow / 关注类事件" in template
    assert "一律 drop" in template
    assert "## 输入" in template  # the swap did not eat the rest of the prompt
