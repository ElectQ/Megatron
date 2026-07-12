"""Bootstrap: auto-configure Megatron on first boot.

Reads config from environment variables and seeds the database.
Idempotent — safe to run on every startup.
"""

from __future__ import annotations

import secrets
import os

from sqlalchemy import select

from .logging import get_logger

logger = get_logger(__name__)


async def bootstrap(db_session) -> None:
    """Run startup bootstrap. All steps are idempotent."""
    from .db import async_session_factory

    async with async_session_factory() as session:
        await _ensure_session_secret()
        await _ensure_admin_user(session)
        await _ensure_llm_provider(session)
        await _ensure_webhook_channel(session)
        # Prompts + tasks come from files under config/ (seeds, create-if-missing),
        # not from Python constants. Provider/channel are seeded first so the tasks
        # can resolve them by name.
        await _seed_profile(session)
        await _sync_sources(session)


async def _seed_profile(session) -> None:
    """Seed prompts and analysis tasks from the config profile. Idempotent."""
    from ..config import settings
    from ..profile.loader import seed_profile

    result = await seed_profile(session, settings.config_dir)
    if result["errors"]:
        logger.error("bootstrap.profile_specs_invalid", errors=result["errors"])


async def _sync_sources(session) -> None:
    """Project the YAML source specs onto source_configs. YAML is the truth."""
    from ..config import settings
    from ..ingest.registry import sync_from_dir

    result = await sync_from_dir(session, settings.sources_dir)
    if result["errors"]:
        # Loud, but not fatal: one broken spec must not stop the other sources
        # (or the whole app) from coming up.
        logger.error("bootstrap.source_specs_invalid", errors=result["errors"])


async def _ensure_session_secret() -> None:
    """Generate session secret, admin token and ingest token if not already set."""
    _persist_or_generate("MEGATRON_SESSION_SECRET", ".session_secret")
    _persist_or_generate("MEGATRON_ADMIN_TOKEN", ".admin_token")
    _persist_or_generate("MEGATRON_INGEST_TOKEN", ".ingest_token")
    _persist_or_generate("MEGATRON_DAY_TOKEN", ".day_token")


def _persist_or_generate(env_var: str, filename: str) -> None:
    """Load from file or env, or generate and persist."""
    if os.getenv(env_var):
        return

    secret_file = f"/app/data/{filename}"
    try:
        if os.path.exists(secret_file):
            with open(secret_file) as f:
                val = f.read().strip()
            if val:
                os.environ[env_var] = val
                return
    except OSError:
        pass

    val = secrets.token_urlsafe(48)
    os.environ[env_var] = val
    try:
        os.makedirs(os.path.dirname(secret_file), exist_ok=True)
        with open(secret_file, "w") as f:
            f.write(val)
    except OSError:
        logger.warning("bootstrap.cannot_persist", path=secret_file)


async def _ensure_admin_user(session) -> None:
    """Create default admin user if none exists."""
    from ..config import settings
    from .engine_models import User
    from .security import generate_token, hash_password

    result = await session.execute(select(User).limit(1))
    if result.scalar_one_or_none():
        return

    password = settings.admin_password
    generated = False
    if not password:
        # No configured password: mint a strong random one instead of the old
        # hardcoded "admin", and log it once so the operator can sign in.
        password = generate_token(18)
        generated = True

    user = User(
        username="admin",
        display_name="Administrator",
        password_hash=hash_password(password),
        is_active=True,
    )
    session.add(user)
    await session.commit()
    if generated:
        logger.warning(
            "bootstrap.admin_user_created_with_generated_password",
            username="admin",
            password=password,
            hint="Set MEGATRON_ADMIN_PASSWORD to control this; change it after first login.",
        )
    else:
        logger.info("bootstrap.admin_user_created", username="admin")


async def _ensure_llm_provider(session) -> None:
    """Create DeepSeek provider if API key is provided and no provider exists."""
    from .engine_models import LLMProvider
    from .security import encrypt_secret

    api_key = os.getenv("MEGATRON_DEEPSEEK_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return

    result = await session.execute(select(LLMProvider).where(LLMProvider.name == "deepseek"))
    if result.scalar_one_or_none():
        # Already exists, maybe update key
        return

    provider = LLMProvider(
        name="deepseek",
        model="deepseek/deepseek-chat",
        api_base="https://api.deepseek.com/v1",
        api_key=encrypt_secret(api_key),
        temperature=0.3,
        max_tokens=32768,
        enabled=True,
    )
    session.add(provider)
    await session.commit()
    logger.info("bootstrap.llm_provider_created")


async def _ensure_webhook_channel(session) -> None:
    """Create DingTalk channel if URL is provided."""
    from .engine_models import WebhookChannel
    from .security import encrypt_config

    webhook_url = os.getenv("MEGATRON_DINGTALK_URL")
    if not webhook_url:
        return

    result = await session.execute(select(WebhookChannel).where(WebhookChannel.kind == "dingtalk"))
    if result.scalar_one_or_none():
        return

    config = {"webhook_url": webhook_url}
    secret = os.getenv("MEGATRON_DINGTALK_SECRET")
    if secret:
        config["secret"] = secret

    channel = WebhookChannel(
        name="钉钉安全简报",
        kind="dingtalk",
        config=encrypt_config(config),
        enabled=True,
    )
    session.add(channel)
    await session.commit()
    logger.info("bootstrap.webhook_channel_created")
