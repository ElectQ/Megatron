from __future__ import annotations

from ...core.types import Item
from .base import BaseFilter, register_filter


@register_filter("dedup")
class DedupFilter(BaseFilter):
    """Collapse retweet chains: keep originals and quote tweets (which carry
    the quoter's commentary), drop pure retweets (no added value).

    threshold: max number of items to keep (0 = no limit).
    """

    name = "dedup"

    def __init__(self, **config):
        super().__init__(**config)
        self.max_keep = int(config.get("max_keep", 0))
        self._seen_content: set[str] = set()

    def score(self, item: Item) -> float:
        # Pure retweet (no quote) is low value; quote tweet keeps commentary.
        if item.is_retweet and not item.is_quote:
            return 0.1
        return 0.9

    def should_include(self, item: Item) -> bool:
        # Drop pure retweets; keep quote tweets (they add commentary).
        if item.is_retweet and not item.is_quote:
            return False
        content_key = item.content.strip().lower()[:200]
        if content_key and content_key in self._seen_content:
            return False
        if content_key:
            self._seen_content.add(content_key)
        return True
