from __future__ import annotations


from ...core.types import Item
from .base import BaseFilter, register_filter


@register_filter("interaction")
class InteractionFilter(BaseFilter):
    """Score by engagement volume using a log-scaled view-adjusted ratio.

    threshold: minimum engagement (raw like+retweet+reply count) to keep.
    """

    name = "interaction"

    def score(self, item: Item) -> float:
        metrics = item.metrics or {}
        views = max(int(metrics.get("view_count", 0)), 1)
        engagement = (
            int(metrics.get("like_count", 0))
            + int(metrics.get("retweet_count", 0))
            + int(metrics.get("reply_count", 0))
        )
        if engagement == 0:
            return 0.0
        ratio = engagement / views
        return round(min(ratio, 1.0), 4)

    def should_include(self, item: Item) -> bool:
        metrics = item.metrics or {}
        engagement = (
            int(metrics.get("like_count", 0))
            + int(metrics.get("retweet_count", 0))
            + int(metrics.get("reply_count", 0))
        )
        return engagement >= self.threshold
