"""0013 — publishing the model's take, and telling the model it is published.

The dangerous state is the half-done one: `why_for_me` rendered on the public
blog while the prompt still promises the model that it will be stripped. Under
that promise the model writes lines aimed at the reader ("你在跑自建 Samba"),
and publishing those is precisely the intent leak the source gate exists to stop.

So the assertion that matters here is not "the prompt changed" — it is that after
`alembic upgrade head`, no stored prompt still contains the stripping promise.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# The promise the old prompts made to the model. Publishing `why_for_me` while
# any prompt still says this is the bug.
STRIP_PROMISE = "由系统自动剥离"

OLD_DAILY = """## 每条必须回填的字段
- `one_liner`：一句话说清**发生了什么**（≤40 字）。
- `why_for_me`：一句话说清**为什么和这个用户有关**（≤35 字）。必须扣住上面的意图，
  不能写成泛泛的"值得关注"。写不出具体关系的，说明它不该是高档位。
- `topics`：标签。
- `public`：**通常不用填**。这条流是一份**公开安全日报**，默认每条都会上公开博客。
  - **只在一条确实敏感时**才显式写 `public: false`。
  - 你不需要担心泄露私人解读：`why_for_me` 和 `scores` 在公开时**由系统自动剥离**，
    博客上只会出现客观事实。

## 输入
共 {{ item_count }} 条：
"""

OLD_RADAR = """## 字段
- `why_for_me`：一句话说清**为什么值得这个用户看**（≤35 字）。
- `public`：`true` / `false`（**默认 false**）。这个仓库值不值得公开分享？
  纯噪音、或你不确定的，留 `false`。（公开时系统会去掉个人 `why_for_me`。）

## 输入
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


@pytest.fixture
def prompts(tmp_path):
    """Both stored prompts as they stood before 0013, brought all the way to head."""
    db = tmp_path / "old.db"
    _alembic(db, "0011_publication_overrides")

    con = sqlite3.connect(db)
    for i, (name, body) in enumerate(
        [("daily_intel_v1", OLD_DAILY), ("github_radar_v1", OLD_RADAR)], start=1
    ):
        con.execute(
            "INSERT INTO prompt_templates "
            "(id, name, display_name, version, template, output_schema, is_active, created_at) "
            "VALUES (?, ?, '', 1, ?, '{}', 1, '2026-01-01')",
            (i, name, body),
        )
    con.commit()
    con.close()

    _alembic(db, "head")
    con = sqlite3.connect(db)
    rows = dict(con.execute("SELECT name, template FROM prompt_templates").fetchall())
    con.close()
    return rows


def test_no_prompt_still_promises_the_model_that_its_take_is_stripped(prompts):
    """The load-bearing one. If this fails, we publish text written in confidence."""
    for name, template in prompts.items():
        assert STRIP_PROMISE not in template, f"{name} still promises stripping"


def test_the_take_is_specified_as_public_and_objective(prompts):
    daily = prompts["daily_intel_v1"]
    assert "这一行会原样出现在公开博客上" in daily
    assert "不要出现「你」「你的」" in daily
    assert "为什么和这个用户有关" not in daily  # the old, reader-addressed framing


def test_the_public_block_now_says_the_take_goes_out_too(prompts):
    daily = prompts["daily_intel_v1"]
    assert "`scores` 不会公开，但 `one_liner` 和 `why_for_me` **会**" in daily


def test_the_github_prompt_stops_asking_for_a_public_call(prompts):
    """That source is locked private at the source level — the field is moot."""
    radar = prompts["github_radar_v1"]
    assert "这条流不上公开博客" in radar
    assert "**默认 false**" not in radar


def test_the_rest_of_each_prompt_survives(prompts):
    """Surgical, not a blanket overwrite."""
    daily = prompts["daily_intel_v1"]
    assert "- `one_liner`：一句话说清**发生了什么**（≤40 字）。" in daily
    assert "- `topics`：标签。" in daily
    assert "共 {{ item_count }} 条：" in daily


def test_it_is_a_no_op_on_a_fresh_database(tmp_path):
    db = tmp_path / "fresh.db"
    _alembic(db, "head")
    con = sqlite3.connect(db)
    assert con.execute("SELECT count(*) FROM prompt_templates").fetchone()[0] == 0
    con.close()
