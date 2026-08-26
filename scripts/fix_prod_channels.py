"""One-shot production repair for the "blank webhook cards" issue.

Background: an admin edit through /ui/tasks replaced the module's
filter_config with only the form fields (time_mode/filters/max_items),
silently dropping task-level keys such as output_mode=day_bundle. Without
day_bundle the runner skips the bundle path, webhook channels fall back to the
legacy render, and the field mismatch (one_liner vs title) produced blank
"1. ****" cards.

This script restores any seed-filter_config keys missing from each module
(without overwriting existing values), and prints the channel wiring so you can
verify both webhooks are bound. Idempotent.

Run inside the app container (from /app, where config/tasks lives):
    docker compose exec -T web python scripts/fix_prod_channels.py
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import yaml
from sqlalchemy import select

from megatron.core.db import async_session_factory
from megatron.core.engine_models import AnalysisModule, ModuleChannel, WebhookChannel


def _seed_filter_config(task_name: str) -> dict:
    p = Path("config/tasks") / f"{task_name}.yaml"
    if not p.is_file():
        return {}
    spec = yaml.safe_load(p.read_text()) or {}
    return spec.get("filter_config") or {}


async def main() -> None:
    async with async_session_factory() as session:
        print("== webhook_channels ==")
        channels = (
            (await session.execute(select(WebhookChannel).order_by(WebhookChannel.id)))
            .scalars()
            .all()
        )
        for ch in channels:
            print(f"  #{ch.id} {ch.name} kind={ch.kind} enabled={ch.enabled}")

        print("\n== modules ==")
        modules = (
            (await session.execute(select(AnalysisModule).order_by(AnalysisModule.id)))
            .scalars()
            .all()
        )
        for m in modules:
            seed_fc = _seed_filter_config(m.name)
            fc = dict(m.filter_config or {})
            added = {k: v for k, v in seed_fc.items() if k not in fc}
            if added:
                fc.update(added)
                m.filter_config = fc
                print(f"  #{m.id} {m.name}: RESTORED {sorted(added)}")
            else:
                print(f"  #{m.id} {m.name}: filter_config ok ({sorted(fc)})")

            # Show effective channel binding (module_channels wins, then legacy column)
            rows = (
                (
                    await session.execute(
                        select(ModuleChannel.channel_id)
                        .where(ModuleChannel.module_id == m.id)
                        .order_by(ModuleChannel.position)
                    )
                )
                .scalars()
                .all()
            )
            bound = [int(r) for r in rows] or [int(r) for r in (m.webhook_channel_ids or [])]
            names = {c.id: f"{c.name}({c.kind})" for c in channels}
            print(f"      bound channels: {[names.get(i, f'#{i}?') for i in bound]}")

        await session.commit()
        print("\nDone. Restart not required (config is DB-driven).")


if __name__ == "__main__":
    asyncio.run(main())
