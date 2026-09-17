"""A provider row must not keep calling a model name DeepSeek retired.

The create-if-missing seed is what makes a rename invisible to a running
instance: the file changes, the row does not. So the fix is split in two, and both
halves are tested here — the data migration that carries existing rows, and the
bootstrap step that stops the next one from landing.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from megatron.core.bootstrap import DEFAULT_DEEPSEEK_MODEL, _ensure_llm_provider
from megatron.core.db import async_session_factory
from megatron.core.engine_models import LLMProvider
from megatron.core.security import decrypt_secret, encrypt_secret

ROOT = Path(__file__).resolve().parents[2]
OLD = "deepseek/deepseek-chat"


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


def _provider(db: Path, name: str, model: str, api_base: str) -> None:
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO llm_providers "
        "(id, name, model, api_base, api_key, temperature, max_tokens, enabled, created_at) "
        "VALUES ((SELECT coalesce(max(id), 0) + 1 FROM llm_providers), ?, ?, ?, 'x', 0.3, 100, 1,"
        " '2026-01-01')",
        (name, model, api_base),
    )
    con.commit()
    con.close()


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "old.db"
    _alembic(path, "0015_github_drop_follow")
    yield path


def _models(path: Path) -> dict[str, str]:
    con = sqlite3.connect(path)
    rows = dict(con.execute("SELECT name, model FROM llm_providers").fetchall())
    con.close()
    return rows


def test_the_retired_name_is_brought_forward(db):
    _provider(db, "deepseek", OLD, "https://api.deepseek.com/v1")
    _alembic(db, "head")
    assert _models(db)["deepseek"] == "deepseek/deepseek-v4-flash"


def test_an_empty_api_base_counts_as_deepseek(db):
    """Empty means litellm's default endpoint, which for a deepseek/ model is theirs."""
    _provider(db, "deepseek", OLD, "")
    _alembic(db, "head")
    assert _models(db)["deepseek"] == "deepseek/deepseek-v4-flash"


def test_a_proxy_keeps_its_own_model_name(db):
    """Pointed at a gateway, the name is whatever that gateway serves — not ours."""
    _provider(db, "gateway", OLD, "https://my-proxy.internal/v1")
    _alembic(db, "head")
    assert _models(db)["gateway"] == OLD


def test_other_models_are_left_alone(db):
    _provider(db, "ds-reasoner", "deepseek/deepseek-reasoner", "")
    _provider(db, "other", "gpt-4o", "")
    _alembic(db, "head")
    assert _models(db)["ds-reasoner"] == "deepseek/deepseek-reasoner"
    assert _models(db)["other"] == "gpt-4o"


def test_it_is_a_no_op_on_a_fresh_database(tmp_path):
    _alembic(tmp_path / "fresh.db", "head")


async def test_bootstrap_repairs_a_retired_name(monkeypatch):
    """Belt and braces: the migration cannot help a row written *after* it ran
    (a stale backup restored over head, for instance)."""
    monkeypatch.setenv("MEGATRON_DEEPSEEK_API_KEY", "sk-test")
    async with async_session_factory() as session:
        session.add(
            LLMProvider(
                name="deepseek",
                model=OLD,
                api_base="https://api.deepseek.com/v1",
                api_key=encrypt_secret("sk-test"),
                temperature=0.3,
                max_tokens=32768,
                enabled=True,
            )
        )
        await session.commit()

    async with async_session_factory() as session:
        await _ensure_llm_provider(session)

    async with async_session_factory() as session:
        row = (
            await session.execute(select(LLMProvider).where(LLMProvider.name == "deepseek"))
        ).scalar_one()
        assert row.model == DEFAULT_DEEPSEEK_MODEL


async def test_bootstrap_leaves_a_chosen_model_alone(monkeypatch):
    """The model is an operator setting (e.g. they moved to deepseek-reasoner).
    Only a retired name is corrected, never a preference."""
    monkeypatch.setenv("MEGATRON_DEEPSEEK_API_KEY", "sk-test")
    async with async_session_factory() as session:
        session.add(
            LLMProvider(
                name="deepseek",
                model="deepseek/deepseek-reasoner",
                api_base="",
                api_key=encrypt_secret("sk-test"),
                temperature=0.9,
                max_tokens=8192,
                enabled=True,
            )
        )
        await session.commit()

    async with async_session_factory() as session:
        await _ensure_llm_provider(session)

    async with async_session_factory() as session:
        row = (
            await session.execute(select(LLMProvider).where(LLMProvider.name == "deepseek"))
        ).scalar_one()
        assert row.model == "deepseek/deepseek-reasoner"
        assert row.temperature == 0.9
        assert decrypt_secret(row.api_key) == "sk-test"
