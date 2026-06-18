from __future__ import annotations

from .base import AnalysisResult, BaseChannel, register_channel


@register_channel("wecom")
class WecomChannel(BaseChannel):
    """WeCom (企业微信) group robot webhook.

    Config: webhook_url
    Delivery: prefers report_markdown (sent as markdown msgtype directly).
    """

    kind = "wecom"

    def endpoint(self) -> str:
        return self.config.get("webhook_url", "")

    def render(self, result: AnalysisResult) -> dict:
        if result.report_markdown:
            return {
                "msgtype": "markdown",
                "markdown": {"content": result.report_markdown[:1900]},
            }
        return self._legacy_render(result)

    def _legacy_render(self, result: AnalysisResult) -> dict:
        lines = [f"⚡ {result.module_name or 'Megatron'} 分析简报\n"]
        if result.briefing:
            lines.append(result.briefing + "\n")
        for i, it in enumerate(result.items or [], 1):
            title = it.get("title", "")
            summary = it.get("summary", "")
            url = it.get("source_url") or it.get("url", "")
            lines.append(f"{i}. **{title}**")
            if summary:
                lines.append(f"   {summary}")
            if url:
                lines.append(f'   <a href="{url}">原文</a>')
            lines.append("")
        return {"msgtype": "markdown", "markdown": {"content": "\n".join(lines)[:1900]}}
