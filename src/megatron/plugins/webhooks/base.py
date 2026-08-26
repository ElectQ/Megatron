from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx

from ...core.logging import get_logger
from ...core.registry import Registry

logger = get_logger(__name__)


@dataclass
class AnalysisResult:
    """Normalized analysis result handed to channels for rendering.

    Two delivery modes:
    - report_markdown: full Markdown briefing, channels send as-is (preferred)
    - briefing + items: fallback when report_markdown is absent (old schema)

    Error handling:
    - parse_error set + report_markdown empty → channels push an error notice
      instead of dumping raw JSON.
    """

    briefing: str
    items: list[dict]
    raw: dict
    run_id: int
    module_name: str = ""
    report_markdown: str = ""
    parse_error: str = ""
    extra: dict = None


class BaseChannel(ABC):
    """Abstract webhook channel. Each subclass renders an AnalysisResult into
    a platform-specific payload and POSTs it. Config is injected per-channel
    instance (tokens/webhooks decrypted at instantiation).

    Platform capability contract:
    - ``max_bytes``: max UTF-8 bytes per single message (0 = unlimited).
      Channels with a limit should use ``split_markdown_bytes`` to split long
      ``report_markdown`` into multiple messages instead of hard-truncating,
      so readers never lose the tail of a digest.

    Known platform limits (2026-08):
      dingtalk markdown ~4500 chars/msg (splits internally)
      wecom     markdown 4096 bytes/msg  (this class: 4000)
      telegram  text     4096 chars/msg
      feishu    text      ~30k chars/msg
      wechat_mp text      2048 chars/msg (hard platform limit)
    """

    kind: str = ""
    max_bytes: int = 0

    def __init__(self, **config: Any):
        self.config = config

    @abstractmethod
    def render(self, result: AnalysisResult) -> Any:
        """Convert result into the platform-specific payload."""
        raise NotImplementedError

    @abstractmethod
    def endpoint(self) -> str:
        """Return the target URL to POST to."""
        raise NotImplementedError

    async def send(self, result: AnalysisResult) -> dict:
        """Render + POST. Returns {ok, status_code, error}.

        Special handling: if result has parse_error and no report_markdown,
        push an error notice instead of dumping raw JSON.
        """
        try:
            if result.parse_error and not result.report_markdown:
                payload = self.render_error(result)
            else:
                payload = self.render(result)
            url = self.endpoint()
            headers = self._headers()
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code < 300:
                logger.info(
                    "channel.sent",
                    kind=self.kind,
                    url=url[:60],
                    status=resp.status_code,
                )
                return {"ok": True, "status_code": resp.status_code, "error": ""}
            return {
                "ok": False,
                "status_code": resp.status_code,
                "error": resp.text[:200],
            }
        except Exception as e:
            logger.error("channel.send_failed", kind=self.kind, error=str(e))
            return {"ok": False, "status_code": 0, "error": str(e)[:200]}

    def render_error(self, result: AnalysisResult) -> dict:
        """Render an error notice when parsing failed.

        Default: plain text error. Channels can override for platform styling.
        """
        return {
            "msgtype": "text",
            "text": {
                "content": f"⚠️ {result.module_name or 'Megatron'} 分析输出异常\n\n请到 Web 后台查看运行 #{result.run_id} 的原始结果。\n\n错误: {result.parse_error[:100]}"
            },
        }

    def _headers(self) -> dict:
        return {"Content-Type": "application/json"}

    async def test(self) -> dict:
        """Send a tiny test message. Override for platform-specific content."""
        test_result = AnalysisResult(
            briefing="✅ Megatron 测试消息 — Webhook 连接正常",
            items=[],
            raw={},
            run_id=0,
            module_name="test",
        )
        return await self.send(test_result)


channel_registry: Registry[BaseChannel] = Registry(kind="channel")


def register_channel(name: str):
    return channel_registry.register(name)


def _severity_icon(severity: str) -> str:
    s = (severity or "").lower()
    if s in ("high", "critical"):
        return "🔴"
    if s in ("medium", "moderate"):
        return "🟡"
    return "🟢"


# Semantic split markers, tried in order so a long digest is cut at
# *meaningful* boundaries (sections first, then list items, then lines):
#   - markdown headings (#, ##, ...)
#   - emoji section markers used by config/digests/*.md (🔴 必看 / 🟡 推荐 / …)
#   - the "——" divider before 查看今日详情
_SECTION_RE = re.compile(r"(?m)^(?=(?:#{1,6} |[🔴🟡🟢🟠🟣🔵⚪]|——))")
# List item lines: "1. …", "- …", "• …"
_ITEM_RE = re.compile(r"(?m)^(?=\d+[.)]\s|[-•]\s)")


def split_markdown_bytes(md: str, max_bytes: int) -> list[str]:
    """Split markdown into chunks that each fit within ``max_bytes`` (UTF-8).

    Splits on line boundaries (never mid-sentence) and hard-splits only an
    overlong single line by bytes. Returns ``[md]`` unchanged when it fits.
    """
    if not md:
        return []
    if max_bytes <= 0 or len(md.encode("utf-8")) <= max_bytes:
        return [md]

    chunks: list[str] = []
    buf = ""
    for line in md.splitlines(keepends=True):
        if buf and len((buf + line).encode("utf-8")) > max_bytes:
            chunks.append(buf)
            buf = ""
        while len(line.encode("utf-8")) > max_bytes:
            take = line
            while take and len(take.encode("utf-8")) > max_bytes:
                take = take[:-1]
            chunks.append(take)
            line = line[len(take):]
        buf += line
    if buf:
        chunks.append(buf)
    return chunks


def split_markdown_sections(md: str, max_bytes: int) -> list[str]:
    """Split markdown at semantic boundaries so each chunk reads as a whole.

    Cut order (most meaningful first):
      1. sections — markdown headings, emoji markers (🔴 必看 / 🟡 推荐), the
         "——" divider before the day link;
      2. list items — "1. …" / "- …" / "• …" lines inside an oversized section;
      3. lines — last resort (via :func:`split_markdown_bytes`).

    Sections are greedily packed up to ``max_bytes`` so short neighbours share
    a message. Returns ``[md]`` unchanged when it fits.
    """
    if not md:
        return []
    if max_bytes <= 0 or len(md.encode("utf-8")) <= max_bytes:
        return [md]

    parts = [p for p in _SECTION_RE.split(md) if p.strip()]
    if len(parts) <= 1:
        parts = [md]

    chunks: list[str] = []
    buf = ""
    for part in parts:
        if buf and len((buf + part).encode("utf-8")) > max_bytes:
            chunks.append(buf)
            buf = ""
        if len(part.encode("utf-8")) > max_bytes:
            # Oversized section: split at list-item boundaries first.
            items = [p for p in _ITEM_RE.split(part) if p.strip()]
            if len(items) <= 1:
                items = [part]
            sub = ""
            for item in items:
                if sub and len((sub + item).encode("utf-8")) > max_bytes:
                    chunks.append(sub)
                    sub = ""
                if len(item.encode("utf-8")) > max_bytes:
                    chunks.extend(c for c in split_markdown_bytes(item, max_bytes) if c)
                    continue
                sub += item
            if sub:
                buf = sub
        else:
            buf += part
    if buf:
        chunks.append(buf)
    return chunks


__all__ = [
    "AnalysisResult",
    "BaseChannel",
    "channel_registry",
    "register_channel",
    "_severity_icon",
    "split_markdown_bytes",
    "split_markdown_sections",
]
