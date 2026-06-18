from __future__ import annotations

import re

from ...core.types import Item
from .base import BaseFilter, register_filter

_CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,}\b", re.IGNORECASE)
_SEV_RE = re.compile(r"\b(0day|zero.?day|RCE|critical|unpatched|in.?the.?wild)\b", re.IGNORECASE)


@register_filter("keyword")
class KeywordFilter(BaseFilter):
    """Boost items mentioning CVEs / severity keywords.

    threshold: minimum boosted score to keep.
    """

    name = "keyword"

    def score(self, item: Item) -> float:
        text = f"{item.title} {item.content}".lower()
        score = 0.2
        if _CVE_RE.search(text):
            score = 0.8
        if _SEV_RE.search(text):
            score = max(score, 0.7)
        if item.tags and any(_CVE_RE.search(t) for t in item.tags):
            score = max(score, 0.85)
        return score
