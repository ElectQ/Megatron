"""Global filtering policy — loaded from `config/policy.yaml`, not hardcoded.

The tunable filtering values a product owner adjusts (the quantity caps and the
political-topic blocklist) live in a file, so changing them is a config edit, not
a framework code change. `engine/bundle.py` keeps only a neutral fallback (empty
blocklist, permissive caps) for the case where no policy file is present — the
real product values come from here and a task can still override per-task via its
`filter_config.caps`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from ..core.logging import get_logger

logger = get_logger(__name__)

# Neutral fallback — deliberately NOT the product's real values. If the policy
# file is missing this keeps the engine running (permissive), rather than baking
# a specific product policy back into code.
_FALLBACK = {
    "caps": {
        "lead_min": 0,
        "must_see_min": 0,
        "must_see_max": 0,
        "recommend_max": 0,
        "skim_max": 0,
    },
    "politics_blocklist": [],
}


@lru_cache(maxsize=8)
def load_policy(path: str = "") -> dict:
    """Read the policy file once (cached). Returns {caps, politics_blocklist}.

    `path` empty → the configured default (`settings.policy_path`). A missing or
    malformed file logs and falls back to the neutral policy.
    """
    if not path:
        from ..config import settings

        path = settings.policy_path

    p = Path(path)
    if not p.is_file():
        logger.info("policy.no_file", path=str(p))
        return {**_FALLBACK, "caps": dict(_FALLBACK["caps"])}

    try:
        import yaml

        raw = yaml.safe_load(p.read_text()) or {}
    except Exception as e:
        logger.error("policy.invalid", path=str(p), error=str(e))
        return {**_FALLBACK, "caps": dict(_FALLBACK["caps"])}

    caps = {**_FALLBACK["caps"], **(raw.get("caps") or {})}
    blocklist = list(raw.get("politics_blocklist") or [])
    return {"caps": caps, "politics_blocklist": blocklist}


def default_caps() -> dict:
    return dict(load_policy()["caps"])


def default_politics() -> tuple[str, ...]:
    return tuple(load_policy()["politics_blocklist"])
