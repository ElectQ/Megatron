from __future__ import annotations


from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.db import insert_ignore
from ..core.logging import get_logger
from ..core.models import IngestLog, ItemRecord
from ..core.types import Item

logger = get_logger(__name__)


class IngestService:
    """Persist Items with idempotent upsert keyed on (source, item_id)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def ingest_items(
        self,
        items: list[Item],
        mode: str = "push",
        date: str = "",
    ) -> tuple[int, int]:
        if not items:
            return 0, 0

        source = items[0].source if items else ""
        source_ref = items[0].source_ref if items else ""

        rows = [
            {
                "item_id": item.id,
                "source": item.source,
                "source_ref": item.source_ref,
                "title": item.title,
                "content": item.content,
                "url": item.url,
                "author": item.author,
                "author_name": item.author_name,
                "language": item.language,
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
            for item in items
        ]

        # One multi-row upsert instead of a round-trip per item; rowcount is the
        # number actually inserted, so the rest were duplicates.
        result = await self.session.execute(insert_ignore(ItemRecord, rows, ["source", "item_id"]))
        ingested = result.rowcount if result.rowcount and result.rowcount > 0 else 0
        duplicated = len(items) - ingested

        self.session.add(
            IngestLog(
                source=source,
                source_ref=source_ref,
                date=date,
                mode=mode,
                ingested=ingested,
                duplicated=duplicated,
            )
        )
        await self.session.commit()

        logger.info(
            "ingest.done",
            source=source,
            source_ref=source_ref,
            mode=mode,
            ingested=ingested,
            duplicated=duplicated,
        )
        return ingested, duplicated

    async def list_items(
        self,
        source: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ItemRecord]:
        stmt = select(ItemRecord).order_by(ItemRecord.published_at.desc())
        if source:
            stmt = stmt.where(ItemRecord.source == source)
        stmt = stmt.limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_item(self, item_id: int) -> ItemRecord | None:
        stmt = select(ItemRecord).where(ItemRecord.id == item_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_existing_keys(self, source: str) -> set[tuple[str, str]]:
        stmt = select(ItemRecord.source, ItemRecord.item_id).where(ItemRecord.source == source)
        result = await self.session.execute(stmt)
        return {(row[0], row[1]) for row in result.all()}


__all__ = ["IngestService"]
