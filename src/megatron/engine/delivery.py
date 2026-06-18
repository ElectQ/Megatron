from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.engine_models import DeliveryLog, WebhookChannel
from ..core.logging import get_logger
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
        channel_ids = list(module.webhook_channel_ids or [])
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

    async def _send_one(self, ch: WebhookChannel, result: AnalysisResult, run_id: int) -> dict:
        if ch.kind not in channel_registry:
            return {"ok": False, "error": f"Unknown channel kind '{ch.kind}'"}
        channel = channel_registry.create(ch.kind, **(ch.config or {}))
        return await channel.send(result)


__all__ = ["DeliveryService"]
