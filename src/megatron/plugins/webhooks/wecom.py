from __future__ import annotations

from ...core.logging import get_logger
from .base import (
    AnalysisResult,
    BaseChannel,
    register_channel,
    split_markdown_sections,
)

logger = get_logger(__name__)

# WeCom group-robot markdown messages are capped at 4096 bytes; stay under it
# with room for the part marker.
MAX_BYTES = 4000


@register_channel("wecom")
class WecomChannel(BaseChannel):
    """WeCom (企业微信) group robot webhook.

    Config: webhook_url
    Delivery: prefers report_markdown (sent as markdown msgtype directly).
    Long digests are split into multiple messages (max_bytes per message).
    """

    kind = "wecom"
    max_bytes = MAX_BYTES

    def endpoint(self) -> str:
        return self.config.get("webhook_url", "")

    def render(self, result: AnalysisResult) -> dict:
        if result.report_markdown:
            return {
                "msgtype": "markdown",
                "markdown": {"content": result.report_markdown},
            }
        return self._legacy_render(result)

    def _legacy_render(self, result: AnalysisResult) -> dict:
        lines = [f"⚡ {result.module_name or 'Megatron'} 分析简报\n"]
        if result.briefing:
            lines.append(result.briefing + "\n")
        for i, it in enumerate(result.items or [], 1):
            # Accept both the legacy (title/summary/source_url) and the
            # day-bundle (one_liner/why_for_me/original_url) field names, so a
            # non-bundle fallback never renders blank cards.
            title = it.get("title") or it.get("one_liner") or ""
            summary = it.get("summary") or it.get("why_for_me") or ""
            url = it.get("source_url") or it.get("url") or it.get("original_url") or ""
            lines.append(f"{i}. **{title}**")
            if summary:
                lines.append(f"   {summary}")
            if url:
                lines.append(f'   <a href="{url}">原文</a>')
            lines.append("")
        return {"msgtype": "markdown", "markdown": {"content": "\n".join(lines)}}

    async def send(self, result: AnalysisResult) -> dict:
        """Render + POST, splitting long markdown into multiple messages."""
        try:
            if result.parse_error and not result.report_markdown:
                return await self._post(self.render_error(result))

            md = result.report_markdown
            if not md:
                # Legacy path (no markdown): single render.
                return await self._post(self.render(result))

            chunks = split_markdown_sections(md, MAX_BYTES)
            total = len(chunks)
            if total <= 1:
                return await self._post(self.render(result))

            last_ok = True
            last_status = 0
            last_error = ""
            for i, chunk in enumerate(chunks, 1):
                content = chunk
                if total > 1 and i > 1:
                    content = f"# （{i}/{total}）\n\n{chunk}"
                payload = {"msgtype": "markdown", "markdown": {"content": content}}
                r = await self._post(payload)
                if not r["ok"]:
                    last_ok = False
                    last_status = r["status_code"]
                    last_error = r["error"]
                    logger.warning("wecom.split.partial_fail", chunk=i, total=total)
            return {"ok": last_ok, "status_code": last_status, "error": last_error}
        except Exception as e:
            logger.error("channel.send_failed", kind=self.kind, error=str(e))
            return {"ok": False, "status_code": 0, "error": str(e)[:200]}

    async def _post(self, payload: dict) -> dict:
        import httpx

        url = self.endpoint()
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                url, json=payload, headers={"Content-Type": "application/json"}
            )
        if resp.status_code < 300:
            logger.info("channel.sent", kind=self.kind, url=url[:60], status=resp.status_code)
            return {"ok": True, "status_code": resp.status_code, "error": ""}
        return {"ok": False, "status_code": resp.status_code, "error": resp.text[:200]}
