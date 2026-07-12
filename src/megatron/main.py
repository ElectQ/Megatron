from __future__ import annotations

import asyncio

import click
import uvicorn


@click.group()
def cli():
    """Megatron - Prompt-driven LLM analysis hub."""
    pass


@cli.group()
def serve():
    """Run the web server."""
    pass


@serve.command()
@click.option("--host", default="0.0.0.0")
@click.option("--port", default=8000, type=int)
@click.option("--reload", is_flag=True, default=False)
def dev(host: str, port: int, reload: bool):
    """Run dev server."""
    uvicorn.run("megatron.web.app:app", host=host, port=port, reload=reload)


@serve.command()
@click.option("--host", default="0.0.0.0")
@click.option("--port", default=8000, type=int)
def prod(host: str, port: int):
    """Run production server."""
    uvicorn.run("megatron.web.app:app", host=host, port=port, workers=1)


@cli.command()
def init():
    """Initialize database tables."""
    from .core.db import dispose_db, init_db

    async def _run():
        await init_db()
        await dispose_db()

    asyncio.run(_run())
    click.echo("Database initialized.")


def _alembic_config():
    from pathlib import Path

    from alembic.config import Config

    from .config import settings

    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    return cfg


@cli.command()
@click.argument("revision", default="head")
def migrate(revision: str):
    """Run database migrations up to REVISION."""
    from alembic import command

    command.upgrade(_alembic_config(), revision)
    click.echo(f"Database migrated to {revision}.")


@cli.command("stamp-db")
@click.argument("revision", default="head")
def stamp_db(revision: str):
    """Mark an existing database as migrated without running DDL."""
    from alembic import command

    command.stamp(_alembic_config(), revision)
    click.echo(f"Database stamped at {revision}.")


@cli.command()
@click.option("--username", prompt=True, help="Login username")
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True, help="Password")
@click.option("--display-name", default="", help="Display name (optional)")
def createsuperuser(username: str, password: str, display_name: str):
    """Create or update a login user."""
    from .core.db import async_session_factory, dispose_db, init_db
    from .core.engine_models import User
    from .core.security import hash_password

    async def _run():
        await init_db()
        async with async_session_factory() as s:
            existing = await s.execute(select(User).where(User.username == username))
            user = existing.scalar_one_or_none()
            if user:
                user.password_hash = hash_password(password)
                if display_name:
                    user.display_name = display_name
                action = "updated"
            else:
                user = User(
                    username=username,
                    password_hash=hash_password(password),
                    display_name=display_name or username,
                    is_active=True,
                )
                s.add(user)
                action = "created"
            await s.commit()
        await dispose_db()
        click.echo(f"User '{username}' {action}.")

    from sqlalchemy import select

    asyncio.run(_run())


@cli.command()
def seed():
    """Seed default prompt templates and a default admin user (admin/admin)."""
    from .core.db import async_session_factory, dispose_db, init_db
    from .core.engine_models import User
    from .core.security import hash_password
    from .engine.builtin import seed_defaults

    async def _run():
        await init_db()
        async with async_session_factory() as s:
            result = await seed_defaults(s)

            existing = await s.execute(select(User).where(User.username == "admin"))
            if not existing.scalar_one_or_none():
                s.add(
                    User(
                        username="admin",
                        password_hash=hash_password("admin"),
                        display_name="Admin",
                        is_active=True,
                    )
                )
                await s.commit()
                click.echo("Default user created: admin / admin (请尽快修改密码!)")
        await dispose_db()
        if result["seeded"]:
            click.echo(f"Seeded: {result['name']} (id={result['id']})")
        else:
            click.echo(f"Skipped prompt: {result['reason']}")

    from sqlalchemy import select

    asyncio.run(_run())


@cli.command()
@click.option("--repo", default="", help="Soundwave repo URL override")
@click.option(
    "--mode",
    type=click.Choice(["auto", "date", "since", "full"]),
    default="auto",
    help="Pull mode: auto (watermark), date, since, full",
)
@click.option("--date", "target_date", default="", help="Specific date YYYY-MM-DD (mode=date)")
@click.option("--since", "since_date", default="", help="Start date YYYY-MM-DD (mode=since)")
@click.option(
    "--full", "full_flag", is_flag=True, default=False, help="Full pull (shorthand for --mode full)"
)
def pull(repo: str, mode: str, target_date: str, since_date: str, full_flag: bool):
    """Pull data from Soundwave repo (git clone).

    \b
    Default: auto mode — pulls new dates since last watermark.
    --date 2026-06-17 : pull only that date
    --since 2026-06-15: pull from that date to today
    --full            : pull all available dates (cold start / rebuild)
    """
    from .config import ingest_settings
    from .core.db import dispose_db, init_db
    from .ingest.puller import GitPuller

    repo_url = repo or ingest_settings.soundwave_repo_url
    if not repo_url:
        raise click.ClickException("No repo URL. Set SOUNDWAVE_REPO_URL or pass --repo")

    if full_flag:
        mode = "full"
    elif target_date:
        mode = "date"
    elif since_date:
        mode = "since"

    async def _run():
        await init_db()
        puller = GitPuller(
            repo_url,
            source="twitter",
            mode=mode,
            target_date=target_date,
            since_date=since_date,
        )
        ingested, duplicated, dates = await puller.run()
        await dispose_db()
        click.echo(f"Pulled: ingested={ingested} duplicated={duplicated} dates={dates or 'all'}")

    asyncio.run(_run())


@cli.group()
def sources():
    """Manage declarative source specs (sources/*.yaml)."""


@sources.command("validate")
def sources_validate():
    """Parse every source spec and report problems. Touches no database."""
    from .config import settings
    from .ingest.registry import load_specs

    specs, errors = load_specs(settings.sources_dir)
    for spec in specs:
        click.echo(f"  ok    {spec.source_id}  ({spec.adapter})")
    for err in errors:
        click.echo(f"  ERROR {err}", err=True)
    click.echo(f"\n{len(specs)} valid, {len(errors)} invalid  [{settings.sources_dir}]")
    if errors:
        raise SystemExit(1)


@sources.command("sync")
def sources_sync():
    """Project sources/*.yaml onto the source_configs table."""
    import asyncio

    from .config import settings
    from .core.db import async_session_factory, dispose_db, init_db
    from .ingest.registry import sync_from_dir

    async def _run():
        await init_db()
        async with async_session_factory() as session:
            result = await sync_from_dir(session, settings.sources_dir)
        await dispose_db()
        for err in result["errors"]:
            click.echo(f"  ERROR {err}", err=True)
        click.echo(
            f"Synced: created={result['created']} updated={result['updated']} "
            f"disabled={result['disabled']}"
        )
        if result["errors"]:
            raise SystemExit(1)

    asyncio.run(_run())


@sources.command("pull")
@click.argument("source_id")
def sources_pull(source_id: str):
    """Poll one source now (bundle_pull / http_pull / git_pull)."""
    import asyncio

    from .core.db import dispose_db, init_db
    from .scheduler import poll_source

    async def _run():
        await init_db()
        try:
            ingested, duplicated = await poll_source(source_id)
        finally:
            await dispose_db()
        click.echo(f"Pulled {source_id}: ingested={ingested} duplicated={duplicated}")

    asyncio.run(_run())


@sources.command("list")
def sources_list():
    """Show the registered sources."""
    import asyncio

    from .core.db import async_session_factory, dispose_db, init_db
    from .ingest.registry import list_sources

    async def _run():
        await init_db()
        async with async_session_factory() as session:
            rows = await list_sources(session, enabled_only=False)
        await dispose_db()
        if not rows:
            click.echo("No sources registered. Add a sources/*.yaml and run `megatron sources sync`.")
            return
        click.echo(f"{'SOURCE_ID':<28} {'ADAPTER':<10} {'MANAGED':<8} {'ENABLED':<8} AUDIENCE")
        for sc in rows:
            click.echo(
                f"{sc.name:<28} {sc.adapter:<10} {sc.managed_by:<8} "
                f"{str(sc.enabled):<8} {sc.audience}"
            )

    asyncio.run(_run())


@cli.command()
def gentoken():
    """Generate a random admin/ingest token for .env."""
    from .core.security import generate_token

    click.echo("MEGATRON_ADMIN_TOKEN=" + generate_token())
    click.echo("MEGATRON_INGEST_TOKEN=" + generate_token())


if __name__ == "__main__":
    cli()
