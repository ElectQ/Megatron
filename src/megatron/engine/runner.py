from __future__ import annotations

import hashlib
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from ..core.models import ItemRecord
from ..core.security import decrypt_secret
from ..core.types import Item
from ..llm.provider import LLMProvider, parse_json_response
from ..plugins.filters.base import filter_registry, run_filters
from .template import render_prompt

logger = get_logger(__name__)


ACTIVE_RUN_STATUSES = ("queued", "running")


class ActiveRunExists(ValueError):
    """Raised when a module already has a queued/running run."""

    def __init__(self, module_id: int, run_id: int, status: str):
        self.module_id = module_id
        self.run_id = run_id
        self.status = status
        super().__init__(f"Module {module_id} already has active run #{run_id} ({status})")


class ModuleRunner:
    """Loads a module config and executes the analysis pipeline.

    Pipeline (P2, no agent tools yet):
        select items from DB -> apply filters -> render prompt -> llm.chat
        -> parse/validate JSON -> persist run
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_run(self, module_id: int, triggered_by: str = "manual") -> dict:
        from ..core.engine_models import AnalysisModule, AnalysisRun

        module = await self.session.get(AnalysisModule, module_id)
        if not module:
            raise ValueError(f"Module {module_id} not found")
        if not module.enabled:
            raise ValueError(f"Module '{module.name}' is disabled")

        active = await self._active_run(module.id)
        if active:
            raise ActiveRunExists(module.id, active.id, active.status)

        run = AnalysisRun(
            module_id=module.id,
            status="queued",
            triggered_by=triggered_by,
        )
        self.session.add(run)
        await self.session.commit()
        await self.session.refresh(run)
        logger.info("runner.queued", module=module.name, run_id=run.id, triggered_by=triggered_by)
        return _run_summary(run)

    async def _active_run(self, module_id: int):
        from ..core.engine_models import AnalysisRun

        return (
            await self.session.execute(
                select(AnalysisRun)
                .where(
                    AnalysisRun.module_id == module_id,
                    AnalysisRun.status.in_(ACTIVE_RUN_STATUSES),
                )
                .order_by(AnalysisRun.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def run_module(self, module_id: int, triggered_by: str = "manual") -> dict:
        """Create a run and execute it immediately.

        Kept for direct engine callers/tests. HTTP/admin entrypoints should
        prefer create_run() + run_run() so requests do not block on LLM work.
        """
        queued = await self.create_run(module_id, triggered_by=triggered_by)
        return await self.run_run(queued["run_id"])

    async def run_run(self, run_id: int) -> dict:
        from ..core.engine_models import AnalysisModule, AnalysisRun

        run = await self.session.get(AnalysisRun, run_id)
        if not run:
            raise ValueError(f"Run {run_id} not found")
        if run.status not in {"queued", "pending"}:
            raise ValueError(f"Run {run_id} is not queued (status={run.status})")

        module = await self.session.get(AnalysisModule, run.module_id)
        if not module:
            run.status = "failed"
            run.error = f"Module {run.module_id} not found"
            run.finished_at = datetime.now(timezone.utc)
            await self.session.commit()
            raise ValueError(run.error)
        if not module.enabled:
            run.status = "failed"
            run.error = f"Module '{module.name}' is disabled"
            run.finished_at = datetime.now(timezone.utc)
            await self.session.commit()
            raise ValueError(run.error)

        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
        await self.session.commit()
        return await self._execute_run(module, run)

    async def _execute_run(self, module, run) -> dict:
        started = time.time()
        try:
            run.module_snapshot = await self._module_snapshot(module)
            run.prompt_snapshot = await self._prompt_snapshot(module)
            run.provider_snapshot = await self._provider_snapshot(module)
            await self.session.commit()

            items = await self._select_items(module)
            run.input_count = len(items)
            run.input_item_ids = [it.id for it in items]
            await self.session.commit()

            if not items:
                run.status = "completed"
                run.result = {"briefing": "无数据", "items": []}
                run.finished_at = datetime.now(timezone.utc)
                run.duration_sec = time.time() - started
                await self.session.commit()
                return _run_summary(run)

            filtered = await self._apply_filters(module, items)
            logger.info(
                "runner.filtered",
                module=module.name,
                before=len(items),
                after=len(filtered),
            )

            prompt_str, prompt_snapshot = await self._render_prompt(module, filtered)

            llm, provider_snapshot = await self._build_llm(module)
            run.prompt_snapshot = prompt_snapshot
            run.provider_snapshot = provider_snapshot
            run.rendered_prompt_hash = hashlib.sha256(prompt_str.encode()).hexdigest()
            await self.session.commit()

            content, tokens_in, tokens_out, cost, tool_log = await self._invoke(
                module, llm, prompt_str
            )

            result = self._parse_result(content)
            run.prompt_tokens = tokens_in
            run.completion_tokens = tokens_out
            run.total_cost_usd = round(cost, 6)
            run.tool_calls = tool_log
            run.result = result
            run.status = "completed"
            run.finished_at = datetime.now(timezone.utc)
            run.duration_sec = round(time.time() - started, 3)
            await self.session.commit()

            deliveries = await self._deliver(module, run, result)
            if deliveries:
                result["deliveries"] = deliveries

            logger.info(
                "runner.done",
                module=module.name,
                run_id=run.id,
                status="completed",
                tokens=tokens_in + tokens_out,
                cost=run.total_cost_usd,
                tool_calls=len(tool_log),
                deliveries=len(deliveries),
            )
            return _run_summary(run)

        except Exception as e:
            run.status = "failed"
            run.error = str(e)
            run.finished_at = datetime.now(timezone.utc)
            run.duration_sec = round(time.time() - started, 3)
            await self.session.commit()
            logger.error("runner.failed", module_id=module.id, run_id=run.id, error=str(e))
            raise

    async def _select_items(self, module) -> list[ItemRecord]:
        """Select items based on filter_config.time_mode.

        Modes (default 'today'):
            today   — collect_date == today (UTC)
            date    — collect_date == filter_config.target_date
            range   — collect_date BETWEEN from AND to
            rolling — published_at >= now - window_hours (legacy)
        """
        fc = module.filter_config or {}
        time_mode = fc.get("time_mode", "today")

        stmt = (
            select(ItemRecord)
            .where(ItemRecord.source == module.source)
            .order_by(ItemRecord.published_at.desc())
        )
        if module.source_ref:
            stmt = stmt.where(ItemRecord.source_ref == module.source_ref)

        today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if time_mode == "date":
            target = fc.get("target_date", today_utc)
            stmt = stmt.where(ItemRecord.collect_date == target)
            logger.info("runner.select.date", target=target)
        elif time_mode == "range":
            date_from = fc.get("date_from", today_utc)
            date_to = fc.get("date_to", today_utc)
            stmt = stmt.where(ItemRecord.collect_date >= date_from).where(
                ItemRecord.collect_date <= date_to
            )
            logger.info("runner.select.range", frm=date_from, to=date_to)
        elif time_mode == "rolling":
            window_hours = int(fc.get("window_hours", 24))
            since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
            stmt = stmt.where(ItemRecord.published_at >= since)
            logger.info("runner.select.rolling", window_hours=window_hours)
        else:
            # today mode (default)
            stmt = stmt.where(ItemRecord.collect_date == today_utc)
            logger.info("runner.select.today", date=today_utc)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def _apply_filters(self, module, records: list[ItemRecord]) -> list[ItemRecord]:
        cfg = module.filter_config.get("filters", [])
        max_items = int(module.filter_config.get("max_items", 30))

        if not cfg:
            # No filters: honor max_items (0 = all)
            return records if max_items <= 0 else records[:max_items]

        filter_objs = []
        for f_cfg in cfg:
            name = f_cfg.get("name")
            if name and name in filter_registry:
                filter_objs.append(filter_registry.create(name, **f_cfg.get("config", {})))

        items = [_record_to_item(r) for r in records]
        scored = run_filters(items, filter_objs)
        if max_items > 0:
            kept_ids = {it.id for it, _ in scored[:max_items]}
        else:
            kept_ids = {it.id for it, _ in scored}

        return [r for r in records if r.item_id in kept_ids]

    async def _render_prompt(self, module, records: list[ItemRecord]) -> tuple[str, dict]:
        from ..core.engine_models import PromptTemplate

        tmpl = await self.session.get(PromptTemplate, module.prompt_template_id)
        if not tmpl:
            raise ValueError(f"PromptTemplate {module.prompt_template_id} not found")
        items = [_record_to_item(r) for r in records]
        snapshot = {
            "id": tmpl.id,
            "name": tmpl.name,
            "version": tmpl.version,
            "template": tmpl.template,
            "output_schema": tmpl.output_schema or {},
            "is_active": tmpl.is_active,
        }
        return render_prompt(tmpl.template, items), snapshot

    async def _prompt_snapshot(self, module) -> dict:
        from ..core.engine_models import PromptTemplate

        tmpl = await self.session.get(PromptTemplate, module.prompt_template_id)
        if not tmpl:
            raise ValueError(f"PromptTemplate {module.prompt_template_id} not found")
        return {
            "id": tmpl.id,
            "name": tmpl.name,
            "version": tmpl.version,
            "template": tmpl.template,
            "output_schema": tmpl.output_schema or {},
            "is_active": tmpl.is_active,
        }

    async def _build_llm(self, module) -> tuple[LLMProvider, dict]:
        from ..core.engine_models import LLMProvider as ProviderModel

        provider = await self.session.get(ProviderModel, module.provider_id)
        if not provider:
            raise ValueError(f"Provider {module.provider_id} not found")
        if not provider.enabled:
            raise ValueError(f"Provider '{provider.name}' is disabled")
        config = {
            "model": provider.model,
            "api_key": decrypt_secret(provider.api_key),
            "api_base": provider.api_base,
            "temperature": provider.temperature,
            "max_tokens": provider.max_tokens,
        }
        snapshot = {
            "id": provider.id,
            "name": provider.name,
            "model": provider.model,
            "api_base": provider.api_base,
            "temperature": provider.temperature,
            "max_tokens": provider.max_tokens,
            "enabled": provider.enabled,
        }
        return LLMProvider(config), snapshot

    async def _provider_snapshot(self, module) -> dict:
        from ..core.engine_models import LLMProvider as ProviderModel

        provider = await self.session.get(ProviderModel, module.provider_id)
        if not provider:
            raise ValueError(f"Provider {module.provider_id} not found")
        if not provider.enabled:
            raise ValueError(f"Provider '{provider.name}' is disabled")
        return {
            "id": provider.id,
            "name": provider.name,
            "model": provider.model,
            "api_base": provider.api_base,
            "temperature": provider.temperature,
            "max_tokens": provider.max_tokens,
            "enabled": provider.enabled,
        }

    async def _module_snapshot(self, module) -> dict:
        return {
            "id": module.id,
            "name": module.name,
            "description": module.description,
            "source": module.source,
            "source_ref": module.source_ref,
            "filter_config": module.filter_config or {},
            "prompt_template_id": module.prompt_template_id,
            "provider_id": module.provider_id,
            "agent_backend": module.agent_backend,
            "tools_config": module.tools_config or [],
            "webhook_channel_ids": await self._module_channel_ids(module),
            "schedule_cron": module.schedule_cron,
            "enabled": module.enabled,
        }

    async def _module_channel_ids(self, module) -> list[int]:
        from ..core.engine_models import ModuleChannel

        rows = (
            await self.session.execute(
                select(ModuleChannel.channel_id)
                .where(ModuleChannel.module_id == module.id)
                .order_by(ModuleChannel.position, ModuleChannel.channel_id)
            )
        ).scalars().all()
        if rows:
            return [int(r) for r in rows]
        return [int(r) for r in (module.webhook_channel_ids or [])]

    async def _invoke(self, module, llm: LLMProvider, prompt_str: str):
        """Dispatch to the configured agent backend.

        Completely config-driven:
        - agent_backend == "none"  -> single-shot chat (no tools)
        - agent_backend in agent_registry -> agent loop with tools from tools_config
        Returns (content, prompt_tokens, completion_tokens, cost, tool_calls).
        """
        from .agent import agent_registry
        from ..plugins.tools.base import ToolSet

        backend = (module.agent_backend or "none").strip()
        if backend == "none" or backend == "":
            resp = await llm.chat([{"role": "user", "content": prompt_str}])
            return resp.content, resp.prompt_tokens, resp.completion_tokens, resp.cost_usd, []

        if backend not in agent_registry:
            logger.warning("runner.unknown_backend", backend=backend, fallback="none")
            resp = await llm.chat([{"role": "user", "content": prompt_str}])
            return resp.content, resp.prompt_tokens, resp.completion_tokens, resp.cost_usd, []

        tool_set = ToolSet.from_config(module.tools_config or [])
        agent_backend_config = (module.filter_config or {}).get("agent", {})
        agent = agent_registry.create(backend, **agent_backend_config)
        logger.info(
            "runner.agent",
            module=module.name,
            backend=backend,
            tools=tool_set.names,
        )
        result = await agent.run(prompt_str, tool_set, llm)
        return (
            result.content,
            result.prompt_tokens,
            result.completion_tokens,
            result.cost_usd,
            result.tool_calls,
        )

    async def _deliver(self, module, run, result: dict) -> list[dict]:
        """Push result to the module's configured webhook channels."""
        from .delivery import DeliveryService
        from ..plugins.webhooks.base import AnalysisResult

        ar = AnalysisResult(
            briefing=result.get("briefing", ""),
            items=result.get("items", []),
            raw=result,
            run_id=run.id,
            module_name=module.name,
            report_markdown=result.get("report_markdown", ""),
        )
        try:
            service = DeliveryService(self.session)
            return await service.deliver(module, run, ar)
        except Exception as e:
            logger.error("runner.delivery_failed", run_id=run.id, error=str(e))
            return []

    def _parse_result(self, content: str) -> dict:
        """Parse LLM output. On failure, never push raw JSON to channels.

        - Success: {"report_markdown": "...", "items": [...]}
        - Partial (markdown salvaged): same shape + "_partial": True
        - Total failure: empty report_markdown + parse_error (channels show error msg)
        """
        try:
            parsed = parse_json_response(content)
            if isinstance(parsed, dict):
                if not parsed.get("briefing") and parsed.get("report_markdown"):
                    parsed["briefing"] = self._extract_briefing(parsed["report_markdown"])
                return parsed
            return {
                "briefing": "",
                "report_markdown": "",
                "items": [],
                "parse_error": "LLM returned non-dict",
            }
        except Exception as e:
            # Last resort: regex extract report_markdown from raw content
            from ..llm.provider import _fallback_extract

            salvaged = _fallback_extract(content)
            if salvaged and salvaged.get("report_markdown"):
                logger.warning("runner.parse_salvaged", error=str(e))
                salvaged["briefing"] = self._extract_briefing(salvaged["report_markdown"])
                salvaged["parse_error"] = f"salvaged: {e}"
                return salvaged
            # Total failure: empty markdown triggers channel error message
            logger.error("runner.parse_failed", error=str(e))
            return {
                "briefing": "",
                "report_markdown": "",
                "items": [],
                "parse_error": str(e)[:200],
            }

    def _extract_briefing(self, markdown: str) -> str:
        """Extract a short briefing fallback from report_markdown (first paragraph under 概述)."""
        lines = markdown.split("\n")
        capture = False
        out = []
        for line in lines:
            if "概述" in line or "概述" in line:
                capture = True
                continue
            if capture:
                if line.strip().startswith("##"):
                    break
                if line.strip():
                    out.append(line.strip())
        return " ".join(out)[:200] if out else markdown[:200]


def _record_to_item(rec: ItemRecord) -> Item:
    return Item(
        id=rec.item_id,
        source=rec.source,
        source_ref=rec.source_ref,
        content=rec.content,
        url=rec.url,
        author=rec.author,
        author_name=rec.author_name,
        title=rec.title,
        language=rec.language,
        published_at=rec.published_at,
        collected_at=rec.collected_at,
        is_retweet=rec.is_retweet,
        is_quote=rec.is_quote,
        tags=rec.tags or [],
        links=rec.links or [],
        media=rec.media or {},
        metrics=rec.metrics or {},
        raw=rec.raw or {},
    )


def _run_summary(run) -> dict:
    return {
        "run_id": run.id,
        "module_id": run.module_id,
        "status": run.status,
        "input_count": run.input_count,
        "prompt_tokens": run.prompt_tokens,
        "completion_tokens": run.completion_tokens,
        "cost_usd": run.total_cost_usd,
        "duration_sec": run.duration_sec,
        "tool_calls": run.tool_calls or [],
        "module_snapshot": run.module_snapshot or {},
        "prompt_snapshot": run.prompt_snapshot or {},
        "provider_snapshot": run.provider_snapshot or {},
        "rendered_prompt_hash": run.rendered_prompt_hash or "",
        "result": run.result,
        "error": run.error,
    }


__all__ = ["ACTIVE_RUN_STATUSES", "ActiveRunExists", "ModuleRunner"]
