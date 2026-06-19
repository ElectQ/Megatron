from __future__ import annotations

import os
import tempfile

# Set a temp DB BEFORE megatron imports its config/engine.
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["MEGATRON_DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp.name}"

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402


@pytest.fixture(scope="session")
def event_loop():
    import asyncio

    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(autouse=True)
async def _reset_db():
    from megatron.core.db import async_session_factory, dispose_db, init_db

    await init_db()
    yield
    async with async_session_factory() as session:
        from sqlalchemy import text

        for table in (
            "delivery_logs",
            "analysis_runs",
            "module_channels",
            "analysis_modules",
            "webhook_channels",
            "prompt_templates",
            "llm_providers",
            "users",
            "items",
            "ingest_logs",
        ):
            await session.execute(text(f"DELETE FROM {table}"))
        await session.commit()
    await dispose_db()
