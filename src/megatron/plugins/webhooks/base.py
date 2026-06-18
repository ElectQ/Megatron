from __future__ import annotations

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
    """

    kind: str = ""

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


__all__ = [
    "AnalysisResult",
    "BaseChannel",
    "channel_registry",
    "register_channel",
    "_severity_icon",
]
