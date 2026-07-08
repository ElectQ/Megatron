"""Shared incremental-pull watermark helpers.

A watermark is the last collection date (``YYYY-MM-DD``) successfully ingested
for a given source, stored in ``PullState`` (keyed by source label). Both the
scheduled ``GitPuller`` and the runner's inline MCP fetch use it to pull only
what is new. These variants operate on a caller-supplied session so they can
participate in the caller's transaction (the caller commits).
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..core.models import PullState


async def get_watermark(session, source: str) -> str:
    """Return the last ingested collect_date for ``source`` ('' if none)."""
    state = await session.get(PullState, source)
    return state.last_date if state else ""


async def advance_watermark(session, source: str, latest_date: str) -> None:
    """Advance the watermark forward to ``latest_date`` (never backwards).

    Does not commit — the caller owns the transaction.
    """
    state = await session.get(PullState, source)
    now = datetime.now(timezone.utc)
    if state:
        if not state.last_date or latest_date > state.last_date:
            state.last_date = latest_date
        state.last_pull_at = now
    else:
        session.add(PullState(source=source, last_date=latest_date, last_pull_at=now))
