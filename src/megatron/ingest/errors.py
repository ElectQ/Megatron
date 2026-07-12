"""Machine-readable ingest failures.

Collectors are CI jobs, not humans: when a push is rejected the failure lands in
a workflow log that somebody reads days later. FastAPI's default 422 body is
awkward to read from a `curl | jq` in a GitHub Action, so every contract
violation here comes back as a 4xx with a flat, stable shape:

    {"detail": {"code": "unsupported_schema_version", "message": "...", "errors": [...]}}
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def ingest_error(
    status_code: int,
    code: str,
    message: str,
    errors: list[Any] | None = None,
) -> HTTPException:
    detail: dict[str, Any] = {"code": code, "message": message}
    if errors:
        detail["errors"] = errors[:20]
    return HTTPException(status_code=status_code, detail=detail)


__all__ = ["ingest_error"]
