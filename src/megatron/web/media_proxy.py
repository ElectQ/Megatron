"""Serve upstream images from our own origin, so the public blog can show them.

Two reasons this exists rather than an `<img src="https://pbs.twimg.com/...">`:

* `pbs.twimg.com` is unreachable from mainland China — the blog's主要读者 — so a
  hotlink renders as a broken image for the people the blog is for.
* A hotlink sends every reader's IP to Twitter. Proxying keeps the reader's
  request on our origin, which is also what the "no CDN, self-contained" design
  of the public pages already promises.

# This endpoint must never become an SSRF hole

An image proxy that takes a URL is, by default, a machine that fetches any URL an
attacker names — including `http://169.254.169.254/` (cloud metadata),
`http://localhost:8000/api/...`, and anything else our server can reach but the
internet cannot. Four gates, and the first one is load-bearing:

1. **HMAC signature.** The URL is not a free parameter: a request carries the URL
   *and* a signature over it, and we recompute the signature with the app secret.
   An attacker cannot produce a valid pair for a URL we never emitted, so the set
   of fetchable URLs is exactly the set that appeared in one of our own pages.
2. **Host allowlist.** Defence in depth: even if the secret leaked, the only
   reachable host is `pbs.twimg.com`.
3. **No redirect following.** Otherwise gate 2 is decorative — upstream answers
   302 and names the target itself.
4. **Response validation.** `Content-Type` must be an image, and the body is
   streamed with a running byte count so a hostile/huge response cannot fill the
   disk.

Nothing here trusts the network. A fetch that fails, times out, or returns
something that is not an image yields a 404, and the templates already render a
card with no picture — the page degrades, it does not break.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from ..config import get_session_secret, settings
from ..core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["media"])

# The only host we will fetch from. Adding one means deciding, deliberately, that
# it is safe to point our server at — not that a new source happens to need it.
ALLOWED_HOSTS = frozenset({"pbs.twimg.com"})

MAX_BYTES = 5 * 1024 * 1024
TIMEOUT = 10.0

# Content types we will serve back. An allowlist, not a "not text/html" check:
# the browser sniffs, and we would rather serve nothing than something exotic.
ALLOWED_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp", "image/avif"})
_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/avif": "avif",
}

# Immutable: a signed URL names exactly one upstream image, and that image never
# changes under it — so the browser may keep it forever.
CACHE_CONTROL = "public, max-age=31536000, immutable"

# Sweep at most this often. The cache only grows when a *new* image is fetched
# (~54/day), so an hourly ceiling means the sweep is effectively free.
_SWEEP_EVERY = 3600.0
_last_sweep = 0.0


def _sign(url: str) -> str:
    """A tag only this install can produce for this exact URL."""
    mac = hmac.new(get_session_secret().encode(), url.encode(), hashlib.sha256)
    return mac.hexdigest()[:16]


def _allowed(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and (parsed.hostname or "") in ALLOWED_HOSTS


def proxied(url: str) -> str:
    """The on-our-origin URL for an upstream image, or "" if we will not serve it.

    Templates call this. Returning "" (rather than raising) is deliberate: an
    item whose media we cannot vouch for simply renders without a picture.
    """
    if not url or not _allowed(url):
        return ""
    packed = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
    return f"/img/{_sign(url)}?u={packed}"


def _unpack(packed: str) -> str:
    pad = "=" * (-len(packed) % 4)
    return base64.urlsafe_b64decode(packed + pad).decode()


def _cache_dir() -> Path:
    return Path(settings.media_cache_dir)


def _cache_path(url: str) -> Path:
    digest = hashlib.sha256(url.encode()).hexdigest()
    return _cache_dir() / digest[:2] / digest


def _cached(url: str) -> Path | None:
    """The cached file for this URL, whatever its extension, or None."""
    stem = _cache_path(url)
    if not stem.parent.is_dir():
        return None
    for path in stem.parent.glob(f"{stem.name}.*"):
        return path
    return None


def _sweep() -> None:
    """Drop the oldest files until the cache is under budget. Best-effort.

    Rate-limited to once an hour: this walks the whole cache dir, and the cache
    only grows on a miss, so running it on every miss would be pure waste.
    """
    global _last_sweep
    now = time.monotonic()
    if now - _last_sweep < _SWEEP_EVERY:
        return
    _last_sweep = now

    budget = settings.media_cache_max_mb * 1024 * 1024
    try:
        files = [
            (p.stat().st_mtime, p.stat().st_size, p) for p in _cache_dir().rglob("*") if p.is_file()
        ]
    except OSError:
        return

    total = sum(size for _, size, _ in files)
    if total <= budget:
        return

    dropped = 0
    for _, size, path in sorted(files):  # oldest first
        if total <= budget:
            break
        try:
            path.unlink()
        except OSError:
            continue
        total -= size
        dropped += 1
    logger.info("media.cache_swept", dropped=dropped, remaining_bytes=total)


async def _fetch(url: str) -> tuple[bytes, str] | None:
    """Fetch an upstream image. None on anything we would not serve."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    return None
                ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                if ctype not in ALLOWED_TYPES:
                    logger.info("media.rejected_type", content_type=ctype)
                    return None

                chunks: list[bytes] = []
                size = 0
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        logger.info("media.too_large", url=url)
                        return None  # abandons the stream — nothing is written
                    chunks.append(chunk)
                return b"".join(chunks), ctype
    except (httpx.HTTPError, OSError) as e:
        logger.info("media.fetch_failed", error=str(e))
        return None


def _store(url: str, body: bytes, ctype: str) -> Path | None:
    """Write to the cache atomically, so a torn write is never served."""
    path = _cache_path(url).with_suffix(f".{_EXT[ctype]}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent)
        with os.fdopen(fd, "wb") as f:
            f.write(body)
        os.replace(tmp, path)
    except OSError as e:
        logger.info("media.cache_write_failed", error=str(e))
        return None
    _sweep()
    return path


def _serve(path: Path) -> FileResponse:
    return FileResponse(path, headers={"Cache-Control": CACHE_CONTROL})


@router.get("/img/{sig}")
async def image(sig: str, u: str = Query(...)):
    """Serve a signed upstream image, from cache when we have it."""
    try:
        url = _unpack(u)
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise HTTPException(status_code=404, detail="Not found")

    # Gate 1 — we only fetch URLs we ourselves emitted. compare_digest, not `==`,
    # so a forger cannot walk the signature out byte by byte.
    if not hmac.compare_digest(sig, _sign(url)):
        raise HTTPException(status_code=403, detail="Bad signature")
    # Gate 2 — and even then, only from a host we chose.
    if not _allowed(url):
        raise HTTPException(status_code=403, detail="Host not allowed")

    hit = _cached(url)
    if hit:
        return _serve(hit)

    fetched = await _fetch(url)
    if not fetched:
        raise HTTPException(status_code=404, detail="Not found")

    stored = _store(url, *fetched)
    if not stored:
        # Cache is unwritable (read-only volume, disk full). Still serve the
        # bytes we already have — a slow page beats a broken one.
        from fastapi.responses import Response

        return Response(fetched[0], media_type=fetched[1], headers={"Cache-Control": CACHE_CONTROL})
    return _serve(stored)


__all__ = ["proxied", "router"]
