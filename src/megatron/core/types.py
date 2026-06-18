from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Item:
    id: str
    source: str
    source_ref: str
    content: str
    url: str
    author: str
    published_at: datetime
    collected_at: datetime
    title: str = ""
    author_name: str = ""
    language: str = ""
    is_retweet: bool = False
    is_quote: bool = False
    # Soundwave 采集日(YYYY-MM-DD),对应 data/ 目录名,用于按天筛选
    collect_date: str = ""
    tags: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    media: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


@dataclass
class IngestBatch:
    """A batch of items pushed/pulled from a source."""

    source: str
    source_ref: str
    date: str
    collected_at: datetime
    items: list[Item] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.items)


__all__ = ["Item", "IngestBatch"]
