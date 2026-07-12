from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
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

_DEFAULT_SOUNDWAVE_REPO = "ElectQ/Soundwave"
_DEFAULT_SOUNDWAVE_BRANCH = "master"

# Adapters whose data arrives out of band (a collector pushes, or the scheduler
# polls). A run reads the database; it never goes to the network for them.
OUT_OF_BAND_ADAPTERS = ("http_push", "http_pull", "bundle_pull", "git_pull", "native")

# Fallback for installs with no registry row yet, so behaviour is unchanged
# until `megatron sources sync` runs.
_LEGACY_KIND_ADAPTER = {
    "twitter": "git_pull",
    "http_pull": "http_pull",
    "bundle_pull": "bundle_pull",
    "soundwave": "mcp_query",
    "mcp": "mcp_query",
}


@dataclass
class SourceBinding:
    """What a module's `source` string resolves to."""

    source_id: str
    adapter: str
    kind: str | None
    kwargs: dict
    row: object | None = None  # SourceConfig | None


def _default_soundwave_repo() -> tuple[str, str]:
    """Derive ``(repo, branch)`` for the built-in Soundwave source from settings.

    Accepts a full GitHub URL or a bare ``owner/repo`` in ``soundwave_repo_url``;
    falls back to the historical default so existing installs keep working.
    """
    from ..config import ingest_settings

    url = (ingest_settings.soundwave_repo_url or "").strip()
    if not url:
        return _DEFAULT_SOUNDWAVE_REPO, _DEFAULT_SOUNDWAVE_BRANCH
    if "github.com/" in url:
        url = url.split("github.com/", 1)[1]
    url = url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    return (url or _DEFAULT_SOUNDWAVE_REPO), _DEFAULT_SOUNDWAVE_BRANCH


class ActiveRunExists(ValueError):
    """Raised when a module already has a queued/running run."""

    def __init__(self, module_id: int, run_id: int, status: str):
        self.module_id = module_id
        self.run_id = run_id
        self.status = status
        super().__init__(f"Module {module_id} already has active run #{run_id} ({status})")


async def reset_interrupted_runs(session) -> int:
    """Mark runs left ``queued``/``running`` by a crash or restart as ``failed``.

    The active-run guard blocks new runs while one is queued/running; without
    this sweep a single crash mid-run would block that module forever.
    """
    from ..core.engine_models import AnalysisRun
    from sqlalchemy import update

    result = await session.execute(
        update(AnalysisRun)
        .where(AnalysisRun.status.in_(ACTIVE_RUN_STATUSES))
        .values(
            status="failed",
            error="interrupted by restart",
            finished_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()
    return result.rowcount or 0


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
        self._warnings: list[dict] = []
        try:
            run.module_snapshot = await self._module_snapshot(module)
            run.prompt_snapshot = await self._prompt_snapshot(module)
            run.provider_snapshot = await self._provider_snapshot(module)
            await self.session.commit()

            # Refresh data from source before selecting. Compute the effective
            # selection window on a COPY — never mutate the persisted
            # module.filter_config (the JSON column isn't change-tracked and an
            # accidental persist would lock the task to a fixed date forever).
            latest_date = await self._refresh_data(module, run)
            effective_fc = dict(module.filter_config or {})
            if latest_date and effective_fc.get("time_mode") in (None, "today", "date"):
                effective_fc = {**effective_fc, "time_mode": "date", "target_date": latest_date}

            await self._check_arrivals(module, effective_fc)

            items = await self._select_items(module, effective_fc)
            run.input_count = len(items)
            run.input_item_ids = [it.id for it in items]
            await self.session.commit()

            if not items:
                run.status = "completed"
                run.result = {"briefing": "无数据", "items": [], "warnings": self._warnings}
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

            prompt_str, prompt_snapshot = await self._render_prompt(
                module, filtered, self._prompt_context(effective_fc)
            )

            llm, provider_snapshot = await self._build_llm(module)
            run.prompt_snapshot = prompt_snapshot
            run.provider_snapshot = provider_snapshot
            run.rendered_prompt_hash = hashlib.sha256(prompt_str.encode()).hexdigest()
            await self.session.commit()

            content, tokens_in, tokens_out, cost, tool_log = await self._invoke(
                module, llm, prompt_str
            )

            result = self._parse_result(content)
            if effective_fc.get("output_mode") == "day_bundle":
                names = await self._source_names(filtered)
                result = self._build_bundle(module, run, effective_fc, filtered, result, names)
            if self._warnings:
                result["warnings"] = self._warnings
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

        except BaseException as e:
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            run.status = "failed"
            run.error = f"{type(e).__name__}: {str(e)[:500]}"
            run.finished_at = datetime.now(timezone.utc)
            run.duration_sec = round(time.time() - started, 3)
            await self.session.commit()
            logger.error("runner.failed", module_id=module.id, run_id=run.id, error=run.error)
            raise

    async def _source_binding(self, module) -> SourceBinding:
        """Resolve ``module.source`` to a registry row, an adapter and plugin kwargs.

        The adapter — not the plugin class — decides whether a run fetches
        anything. See ``_refresh_data``.
        """
        kind, kwargs = await self._resolve_source(module)

        from sqlalchemy import select as sa_select

        from ..core.models import SourceConfig

        row = (
            await self.session.execute(
                sa_select(SourceConfig).where(SourceConfig.name == module.source)
            )
        ).scalar_one_or_none()

        if row is not None and row.adapter:
            adapter = row.adapter
            if adapter in ("http_pull", "bundle_pull"):
                # The plugin kind is implied by the adapter; the spec's fetch/map
                # (or index_url) blocks live in config and are already in kwargs.
                kind = adapter
        else:
            # No registry row: infer from the built-in kind so an install that
            # has not run `sources sync` still behaves as it did before.
            adapter = _LEGACY_KIND_ADAPTER.get(kind or "", "native")

        return SourceBinding(
            source_id=module.source,
            adapter=adapter,
            kind=kind,
            kwargs=kwargs,
            row=row,
        )

    async def _resolve_source(self, module) -> tuple[str | None, dict]:
        """Resolve a module's data source to ``(source_kind, kwargs)``.

        ``module.source`` may name either a plugin kind (built-in
        ``twitter``/``soundwave``/``mcp``) or a configured ``SourceConfig`` row.
        We always inject ``source_label = module.source`` so ingested items are
        tagged with a label that ``_select_items`` will match by construction.

        Precedence:
            1. A matching enabled ``SourceConfig`` (``name == module.source``):
               its decrypted ``config`` supplies kwargs; for ``mcp`` type the
               linked ``MCPServer`` supplies transport + address (server_url for
               SSE, or a command line split into command/args for stdio).
            2. A built-in registry kind; ``soundwave``/``mcp`` get repo/branch
               defaults so existing installs keep working.
        """
        import shlex

        from sqlalchemy import select as sa_select

        from ..core.models import MCPServer, SourceConfig
        from ..core.security import decrypt_config
        from ..plugins.sources.base import source_registry

        source_name = module.source
        kwargs: dict = {"source_label": source_name}

        sc = (
            await self.session.execute(
                sa_select(SourceConfig).where(
                    SourceConfig.name == source_name,
                    SourceConfig.enabled.is_(True),
                )
            )
        ).scalar_one_or_none()

        if sc:
            cfg = dict(decrypt_config(sc.config or {}))
            if sc.source_type == "mcp":
                server_id = cfg.pop("mcp_server_id", None)
                cfg.pop("mcp_server_name", None)
                kwargs.update(cfg)
                if server_id is not None:
                    srv = (
                        await self.session.execute(
                            sa_select(MCPServer).where(MCPServer.id == server_id)
                        )
                    ).scalar_one_or_none()
                    if srv:
                        kwargs.setdefault("transport", srv.transport)
                        url = srv.server_url or ""
                        if srv.transport == "sse":
                            kwargs.setdefault("server_url", url)
                        elif " " in url:  # stdio command line
                            parts = shlex.split(url)
                            if parts:
                                kwargs.setdefault("command", parts[0])
                                kwargs.setdefault("args", parts[1:])
                        elif "/" in url:  # owner/repo → bundled soundwave
                            kwargs.setdefault("repo", url)
                return "mcp", kwargs
            # Native (non-MCP) source config: the plugin kind lives in config.
            kind = cfg.pop("plugin_name", source_name)
            kwargs.update(cfg)
            return kind, kwargs

        # No SourceConfig: fall back to a built-in registry kind.
        if source_name in source_registry:
            if source_name in ("soundwave", "mcp"):
                repo, branch = _default_soundwave_repo()
                kwargs.setdefault("repo", repo)
                kwargs.setdefault("branch", branch)
            return source_name, kwargs

        return None, kwargs

    async def _check_arrivals(self, module, fc: dict) -> None:
        """Warn about sources that never showed up. Do not stop the run.

        Waiting for a full house means one dead collector costs you the whole
        day's brief. Publish with what arrived and say what did not — unless the
        task explicitly asks to fail instead.
        """
        from ..ingest.health import missing_sources, today_arrivals

        date = fc.get("target_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        wanted = set(fc.get("sources") or [module.source])

        arrivals = await today_arrivals(self.session, date)
        missing = [a for a in missing_sources(arrivals) if a.source_id in wanted]
        if not missing:
            return

        names = ", ".join(a.source_id for a in missing)
        if fc.get("fail_on_missing_source"):
            raise RuntimeError(f"No data for {date} from: {names}")

        self._warn(
            "source_missing",
            f"No data for {date} from: {names}",
            date=date,
            sources=[a.source_id for a in missing],
        )

    async def _source_names(self, records) -> dict[str, str]:
        """source_id -> display name, so the digest is titled by its source
        rather than by a string baked into the renderer."""
        from sqlalchemy import select as sa_select

        from ..core.models import SourceConfig

        ids = {r.source for r in records}
        if not ids:
            return {}
        rows = (
            (await self.session.execute(sa_select(SourceConfig).where(SourceConfig.name.in_(ids))))
            .scalars()
            .all()
        )
        return {sc.name: (sc.display_name or sc.name) for sc in rows}

    def _build_bundle(self, module, run, fc: dict, records, llm_output: dict, source_names) -> dict:
        """Turn the model's tiering into a day bundle, with the caps enforced here.

        Opt-in via filter_config.output_mode == "day_bundle" so tasks that predate
        this keep their old result shape.
        """
        from ..config import get_day_token, settings
        from .bundle import build_day_bundle
        from .doorbell import render_doorbell
        from .validate import validate_output

        # Production refuses to boot on a loopback base_url; a dev box does not, so
        # say it on the run rather than let someone wonder why the link is dead.
        if settings.base_url_is_local:
            self._warn(
                "base_url_not_reachable",
                f"MEGATRON_BASE_URL={settings.base_url} — the 详情 link in the push only "
                "opens on this machine. Set it to the address readers reach you at.",
            )

        date = fc.get("target_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        schema = (run.prompt_snapshot or {}).get("output_schema") or {}
        schema_errors = validate_output(llm_output, schema)
        if schema_errors:
            self._warn(
                "schema_errors",
                f"LLM output did not match the prompt's output_schema ({len(schema_errors)})",
                errors=schema_errors[:3],
            )

        bundle = build_day_bundle(
            date=date,
            run_id=run.id,
            records=records,
            llm_output=llm_output,
            caps=self._effective_caps(fc),
            intent=fc.get("intent") or {},
            base_url=settings.base_url,
            day_token=get_day_token(),
            timezone=fc.get("timezone", "Asia/Shanghai"),
            source_names=source_names,
            title=fc.get("title", ""),
        )
        bundle["schema_errors"] = schema_errors
        # The task picks the push template (like page_layout picks the day page):
        # `digest` = tiered push, `feed` = link-only for page-only sources.
        bundle["digest_style"] = fc.get("digest_style", "digest")
        # Channels already prefer report_markdown; handing them the rendered push
        # means the webhook plugins need no changes at all.
        bundle["report_markdown"] = render_doorbell(bundle)

        logger.info(
            "runner.bundle",
            run_id=run.id,
            date=date,
            items=len(bundle["items"]),
            push=len(bundle["push_item_ids"]),
            unmatched=bundle["stats"]["dropped_unmatched"],
            schema_errors=len(schema_errors),
        )
        return bundle

    def _warn(self, code: str, message: str, **fields) -> None:
        """Record a run-level warning. Surfaced in run.result['warnings']."""
        self._warnings.append({"code": code, "message": message, **fields})
        logger.warning(f"runner.{code}", **fields)

    async def _refresh_data(self, module, run) -> str | None:
        """Bring the day's data in, if this source needs it. Returns the latest date.

        The adapter decides:

        * http_push / http_pull / git_pull / native — data arrives out of band
          (a collector pushes, or the scheduler polls). A run reads the DB.
        * mcp_query — the degraded path. Pulling a whole day over MCP inside the
          run and analysing it is the thing the spec explicitly rejects, so it is
          gated on MEGATRON_MCP_LIVE_FETCH:

            off       never; the source is for querying, not for the daily job
            backfill  only when the day has zero rows — top the DB up, then read
                      the DB like everyone else (default, transitional)
            always    the old behaviour; an escape hatch, not a destination

        `backfill` is not "fetch a day and analyse it": it repairs the table and
        the analysis still reads the table. It exists because today the only
        working intake for this install *is* MCP, and turning it off before the
        collector pushes would leave the daily brief empty.
        """
        binding = await self._source_binding(module)

        if binding.adapter in OUT_OF_BAND_ADAPTERS:
            return None

        if binding.adapter != "mcp_query":
            logger.warning(
                "runner.refresh.unknown_adapter",
                source=binding.source_id,
                adapter=binding.adapter,
            )
            return None

        return await self._mcp_backfill(module, binding)

    async def _mcp_backfill(self, module, binding: SourceBinding) -> str | None:
        from datetime import timedelta

        from ..config import get_ingest_settings
        from ..core.db import insert_ignore
        from ..core.models import ItemRecord
        from ..ingest.watermark import advance_watermark, get_watermark
        from ..plugins.sources.base import source_registry

        mode = (get_ingest_settings().mcp_live_fetch or "backfill").lower()

        if mode == "off":
            self._warn(
                "mcp_live_fetch_disabled",
                f"Source '{binding.source_id}' is adapter=mcp_query and "
                "MEGATRON_MCP_LIVE_FETCH=off; its data must arrive via ingest.",
                source=binding.source_id,
            )
            return None

        if mode == "backfill":
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            existing = (
                await self.session.execute(
                    select(func.count())
                    .select_from(ItemRecord)
                    .where(
                        ItemRecord.source == binding.source_id,
                        ItemRecord.collect_date == today,
                    )
                )
            ).scalar_one()
            if existing:
                # The collector already delivered; do not touch the network.
                return None
            self._warn(
                "mcp_backfill",
                f"No rows for {today} from '{binding.source_id}'; backfilling over MCP. "
                "Switch the collector to HTTP push and set MEGATRON_MCP_LIVE_FETCH=off.",
                source=binding.source_id,
                date=today,
            )

        kind, source_kwargs = binding.kind, binding.kwargs
        if not kind or kind not in source_registry:
            logger.warning("runner.refresh.no_source", source=module.source)
            return None
        source_cls = source_registry.get(kind)

        # Incremental: fetch only dates strictly after our watermark.
        wm = await get_watermark(self.session, module.source)
        since = None
        if wm:
            since = datetime.strptime(wm, "%Y-%m-%d") + timedelta(days=1)

        source = source_cls(**source_kwargs)
        try:
            items = await source.fetch(since=since)  # raises → run fails
        finally:
            await source.close()

        if not items:
            logger.info("runner.refresh.no_items", source=module.source)
            return None

        rows = []
        latest_date = items[0].collect_date
        for item in items:
            if item.collect_date > latest_date:
                latest_date = item.collect_date
            rows.append(
                {
                    "item_id": item.id,
                    "source": item.source,
                    "source_ref": item.source_ref,
                    "content": item.content,
                    "url": item.url,
                    "author": item.author,
                    "author_name": item.author_name,
                    "published_at": item.published_at,
                    "collected_at": item.collected_at,
                    "collect_date": item.collect_date,
                    "is_retweet": item.is_retweet,
                    "is_quote": item.is_quote,
                    "tags": item.tags,
                    "links": item.links,
                    "media": item.media,
                    "metrics": item.metrics,
                    "raw": item.raw,
                }
            )

        await self.session.execute(insert_ignore(ItemRecord, rows, ["source", "item_id"]))
        await advance_watermark(self.session, module.source, latest_date)
        await self.session.commit()
        logger.info(
            "runner.refresh.done",
            source=module.source,
            fetched=len(rows),
            latest_date=latest_date,
        )
        return latest_date

    async def _select_items(self, module, fc: dict | None = None) -> list[ItemRecord]:
        """Select items based on time_mode.

        ``fc`` is the effective filter config (a copy computed by the caller so
        the persisted ``module.filter_config`` is never mutated); falls back to
        the module's own config.

        Modes (default 'today'):
            today   — collect_date == today (UTC)
            date    — collect_date == filter_config.target_date
            range   — collect_date BETWEEN from AND to
            rolling — published_at >= now - window_hours (legacy)
        """
        fc = fc if fc is not None else (module.filter_config or {})
        time_mode = fc.get("time_mode", "today")

        # `sources` lets a day pipeline merge several collectors; unset keeps the
        # historical single-source behaviour exactly.
        sources = fc.get("sources") or [module.source]
        stmt = (
            select(ItemRecord)
            .where(ItemRecord.source.in_(sources))
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

        # A day bundle is meant to be the day's complete view, so by default it
        # sends everything to the model. Truncating the input to 30 would silently
        # decide what the digest may contain before the model ever sees it — and
        # a source can easily deliver 150 items a day.
        default_max = 0 if module.filter_config.get("output_mode") == "day_bundle" else 30
        max_items = int(module.filter_config.get("max_items", default_max))

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

    def _effective_caps(self, fc: dict) -> dict:
        """Caps actually applied: policy defaults + policy politics list, then the
        task's own overrides. The product filtering policy comes from
        config/policy.yaml — not from a framework constant."""
        from ..profile.policy import default_caps, default_politics

        base = default_caps()
        base.setdefault("politics_blocklist", list(default_politics()))
        return {**base, **(fc.get("caps") or {})}

    def _prompt_context(self, fc: dict) -> dict:
        """What the prompt gets to know about the run, beyond the items.

        The caps go in so the prompt's stated limits and the limits
        ``enforce_caps`` will actually apply are the same numbers. They are still
        enforced server-side afterwards — telling the model the budget just stops
        it from writing a digest that is guaranteed to be cut apart.
        """
        return {
            "intent": fc.get("intent") or {},
            "caps": self._effective_caps(fc),
            "date": fc.get("target_date") or "",
        }

    async def _render_prompt(
        self, module, records: list[ItemRecord], extra: dict | None = None
    ) -> tuple[str, dict]:
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
        return render_prompt(tmpl.template, items, extra), snapshot

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
            (
                await self.session.execute(
                    select(ModuleChannel.channel_id)
                    .where(ModuleChannel.module_id == module.id)
                    .order_by(ModuleChannel.position, ModuleChannel.channel_id)
                )
            )
            .scalars()
            .all()
        )
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
        """Push to the module's channels.

        For a day bundle the channels only ever see the capped push subset and
        the doorbell text — the rest of the day lives on the day page. The
        webhook plugins are unchanged: they already prefer `report_markdown`.
        """
        from ..plugins.webhooks.base import AnalysisResult
        from .bundle import BUNDLE_SCHEMA, push_items
        from .delivery import DeliveryService

        is_bundle = result.get("schema") == BUNDLE_SCHEMA
        items = push_items(result) if is_bundle else result.get("items", [])

        ar = AnalysisResult(
            briefing=result.get("briefing", ""),
            items=items,
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
