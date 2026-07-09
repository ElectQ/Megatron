from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.engine_models import DeliveryLog, ModuleChannel, WebhookChannel
from ..core.logging import get_logger
from ..core.security import decrypt_config
from ..plugins.webhooks.base import AnalysisResult, channel_registry

logger = get_logger(__name__)


class DeliveryService:
    """Deliver an analysis result to the channels configured on a module.

    Fully config-driven: reads module.webhook_channel_ids, instantiates each
    channel from its (decrypted) DB config, sends, and logs the outcome.
    The service has zero platform-specific logic — that lives in the channel
    plugins.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def deliver(
        self,
        module,
        run,
        result: AnalysisResult,
    ) -> list[dict]:
        channel_ids = await self._channel_ids(module)
        if not channel_ids:
            logger.info("delivery.skip", reason="no channels on module", module=module.name)
            return []

        stmt = select(WebhookChannel).where(WebhookChannel.id.in_(channel_ids))
        rows = (await self.session.execute(stmt)).scalars().all()

        outcomes: list[dict] = []
        for ch in rows:
            if not ch.enabled:
                continue
            outcome = await self._send_one(ch, result, run.id)
            self.session.add(
                DeliveryLog(
                    run_id=run.id,
                    channel_id=ch.id,
                    channel_name=ch.name,
                    status="sent" if outcome["ok"] else "failed",
                    error=outcome.get("error", ""),
                )
            )
            outcomes.append({"channel": ch.name, "kind": ch.kind, **outcome})

        await self.session.commit()
        logger.info(
            "delivery.done",
            run_id=run.id,
            channels=len(outcomes),
            ok=sum(1 for o in outcomes if o["ok"]),
        )
        return outcomes

    async def _channel_ids(self, module) -> list[int]:
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

    async def _send_one(self, ch: WebhookChannel, result: AnalysisResult, run_id: int) -> dict:
        if ch.kind not in channel_registry:
            return {"ok": False, "error": f"Unknown channel kind '{ch.kind}'"}
        # Build + send inside one guard so a single bad channel (unparseable
        # config, network error) is logged as a failed delivery rather than
        # bubbling up and rolling back the whole batch of DeliveryLog rows.
        try:
            channel = channel_registry.create(ch.kind, **decrypt_config(ch.config or {}))
            return await channel.send(result)
        except Exception as e:
            logger.error("delivery.send_failed", channel=ch.name, kind=ch.kind, error=str(e))
            return {"ok": False, "error": str(e)[:500]}


__all__ = ["DeliveryService"]
