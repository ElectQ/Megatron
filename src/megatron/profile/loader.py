"""Load the product profile (prompts, tasks) from files and seed the DB.

Mirrors `ingest/registry.py` — glob a directory, parse each file into a spec,
report-and-skip a broken one rather than crashing boot. The difference is the
projection mode: sources are *authoritative* (overwrite the DB), while prompts and
tasks are *seeds* — created only when absent, so admin-UI edits win.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from .spec import PromptSpec, TaskSpec

logger = get_logger(__name__)


class SpecError(ValueError):
    def __init__(self, path: Path, message: str):
        self.path = path
        super().__init__(f"{path}: {message}")


# ---------------------------------------------------------------- parsing


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """A `--- yaml --- body` markdown file → (frontmatter dict, body)."""
    import yaml

    if not text.startswith("---"):
        raise ValueError("prompt file must start with a `---` frontmatter block")
    _, _, rest = text.partition("---\n")
    fm_text, sep, body = rest.partition("\n---")
    if not sep:
        raise ValueError("unterminated frontmatter block (missing closing `---`)")
    fm = yaml.safe_load(fm_text) or {}
    if not isinstance(fm, dict):
        raise ValueError("frontmatter must be a mapping")
    return fm, body.lstrip("\n")


def load_prompt_specs(prompts_dir: str | Path) -> tuple[list[PromptSpec], list[SpecError]]:
    root = Path(prompts_dir)
    if not root.is_dir():
        logger.info("prompts.no_dir", path=str(root))
        return [], []
    specs, errors, seen = [], [], {}
    for path in sorted(root.glob("*.md")):
        try:
            fm, body = _split_frontmatter(path.read_text())
            spec = PromptSpec(body=body, **fm)
        except Exception as e:
            errors.append(SpecError(path, str(e)))
            continue
        if spec.name in seen:
            errors.append(SpecError(path, f"duplicate prompt name '{spec.name}'"))
            continue
        seen[spec.name] = path
        specs.append(spec)
    logger.info("prompts.loaded", path=str(root), specs=len(specs), errors=len(errors))
    return specs, errors


def load_task_specs(tasks_dir: str | Path) -> tuple[list[TaskSpec], list[SpecError]]:
    import yaml

    root = Path(tasks_dir)
    if not root.is_dir():
        logger.info("tasks.no_dir", path=str(root))
        return [], []
    specs, errors, seen = [], [], {}
    for path in sorted([*root.glob("*.yaml"), *root.glob("*.yml")]):
        try:
            raw = yaml.safe_load(path.read_text()) or {}
            if not isinstance(raw, dict):
                raise ValueError("expected a mapping at the top level")
            spec = TaskSpec(**raw)
        except Exception as e:
            errors.append(SpecError(path, str(e)))
            continue
        if spec.name in seen:
            errors.append(SpecError(path, f"duplicate task name '{spec.name}'"))
            continue
        seen[spec.name] = path
        specs.append(spec)
    logger.info("tasks.loaded", path=str(root), specs=len(specs), errors=len(errors))
    return specs, errors


# ---------------------------------------------------------------- seeding


async def seed_prompts(session: AsyncSession, specs: list[PromptSpec]) -> dict:
    """Create a PromptTemplate for each spec that has no row yet. UI edits win."""
    from ..core.engine_models import PromptTemplate
    from ..engine.builtin import schema_for

    seeded, skipped = [], []
    for spec in specs:
        exists = (
            (await session.execute(select(PromptTemplate).where(PromptTemplate.name == spec.name)))
            .scalars()
            .first()
        )
        if exists:
            skipped.append(spec.name)
            continue
        session.add(
            PromptTemplate(
                name=spec.name,
                display_name=spec.display_name or spec.name,
                version=1,
                template=spec.body,
                output_schema=schema_for(spec.output_schema),
                is_active=True,
            )
        )
        seeded.append(spec.name)
    if seeded:
        await session.commit()
    logger.info("prompts.seeded", seeded=seeded, skipped=len(skipped))
    return {"seeded": seeded, "skipped": skipped}


async def seed_tasks(session: AsyncSession, specs: list[TaskSpec]) -> dict:
    """Create an AnalysisModule for each spec with no row yet, resolving names→ids."""
    from ..core.engine_models import AnalysisModule, LLMProvider, PromptTemplate, WebhookChannel

    seeded, skipped, warned = [], [], []
    for spec in specs:
        exists = (
            (await session.execute(select(AnalysisModule).where(AnalysisModule.name == spec.name)))
            .scalars()
            .first()
        )
        if exists:
            skipped.append(spec.name)
            continue

        prompt = (
            (
                await session.execute(
                    select(PromptTemplate).where(PromptTemplate.name == spec.prompt)
                )
            )
            .scalars()
            .first()
        )
        provider = (
            await session.execute(select(LLMProvider).where(LLMProvider.name == spec.provider))
        ).scalar_one_or_none()
        if not prompt or not provider:
            # No LLM key yet, or prompt seeding skipped: nothing to attach to.
            logger.info(
                "tasks.skip_unresolved",
                task=spec.name,
                has_prompt=bool(prompt),
                has_provider=bool(provider),
            )
            warned.append(spec.name)
            continue

        channel_ids = []
        for cname in spec.channels:
            ch = (
                (await session.execute(select(WebhookChannel).where(WebhookChannel.name == cname)))
                .scalars()
                .first()
            )
            if ch:
                channel_ids.append(ch.id)
            else:
                logger.info("tasks.channel_missing", task=spec.name, channel=cname)

        session.add(
            AnalysisModule(
                name=spec.name,
                description=spec.description,
                source=spec.source,
                source_ref="",
                filter_config=spec.filter_config,
                prompt_template_id=prompt.id,
                provider_id=provider.id,
                agent_backend="none",
                tools_config=[],
                webhook_channel_ids=channel_ids,
                schedule_cron=spec.schedule_cron,
                enabled=spec.enabled,
            )
        )
        seeded.append(spec.name)
    if seeded:
        await session.commit()
    logger.info("tasks.seeded", seeded=seeded, skipped=len(skipped), unresolved=warned)
    return {"seeded": seeded, "skipped": skipped, "unresolved": warned}


async def resolve_digest_body(session: AsyncSession, style: str, config_dir: str | Path) -> str:
    """The digest template body: the editable DB row if present, else the file.

    Mirrors resolve_policy — DB is truth after seeding, file is the seed/fallback.
    An unknown style falls back to the default `digest` template.
    """
    from ..core.engine_models import DigestTemplate

    row = (
        (await session.execute(select(DigestTemplate).where(DigestTemplate.style == style)))
        .scalars()
        .first()
    )
    if row is not None:
        return row.body

    root = Path(config_dir) / "digests"
    for name in (f"{style}.md", "digest.md"):
        path = root / name
        if path.is_file():
            return path.read_text()
    return "⚡ {{ title }} · {{ date }}\n{% if day_url %}[详情]({{ day_url }}){% endif %}"


async def seed_digests(session: AsyncSession, digests_dir: str | Path) -> dict:
    """Create a DigestTemplate for each config/digests/<style>.md with no row yet."""
    from ..core.engine_models import DigestTemplate

    root = Path(digests_dir)
    if not root.is_dir():
        return {"seeded": [], "skipped": []}
    seeded, skipped = [], []
    for path in sorted(root.glob("*.md")):
        style = path.stem
        exists = (
            (await session.execute(select(DigestTemplate).where(DigestTemplate.style == style)))
            .scalars()
            .first()
        )
        if exists:
            skipped.append(style)
            continue
        session.add(
            DigestTemplate(
                style=style,
                display_name={"digest": "分档推送", "feed": "仅链接推送"}.get(style, style),
                body=path.read_text(),
                is_active=True,
            )
        )
        seeded.append(style)
    if seeded:
        await session.commit()
    logger.info("digests.seeded", seeded=seeded, skipped=len(skipped))
    return {"seeded": seeded, "skipped": skipped}


async def seed_policy(session: AsyncSession, policy_path: str | Path) -> dict:
    """Seed the single Policy row from config/policy.yaml if the table is empty."""
    from ..core.engine_models import Policy
    from .policy import load_policy

    exists = (await session.execute(select(Policy))).scalars().first()
    if exists:
        return {"seeded": False}
    pol = load_policy(str(policy_path))
    session.add(Policy(caps=pol["caps"], politics_blocklist=pol["politics_blocklist"]))
    await session.commit()
    logger.info("policy.seeded", caps=pol["caps"], terms=len(pol["politics_blocklist"]))
    return {"seeded": True}


async def seed_profile(session: AsyncSession, config_dir: str | Path) -> dict:
    """Load + seed prompts, tasks, digests and policy from a profile dir. Idempotent."""
    root = Path(config_dir)
    p_specs, p_err = load_prompt_specs(root / "prompts")
    for e in p_err:
        logger.error("prompts.spec_invalid", error=str(e))
    prompts = await seed_prompts(session, p_specs)

    t_specs, t_err = load_task_specs(root / "tasks")
    for e in t_err:
        logger.error("tasks.spec_invalid", error=str(e))
    tasks = await seed_tasks(session, t_specs)

    digests = await seed_digests(session, root / "digests")
    policy = await seed_policy(session, root / "policy.yaml")

    return {
        "prompts": prompts,
        "tasks": tasks,
        "digests": digests,
        "policy": policy,
        "errors": [str(e) for e in (p_err + t_err)],
    }
