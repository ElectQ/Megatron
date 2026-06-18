from __future__ import annotations

from .base import AnalysisResult, BaseChannel, register_channel


@register_channel("telegram")
class TelegramChannel(BaseChannel):
    """Telegram Bot API channel.

    Config: bot_token, chat_id
    Delivery: prefers report_markdown (sent as Markdown, as-is); falls back to
    briefing+items render for legacy outputs.
    """

    kind = "telegram"

    @property
    def bot_token(self) -> str:
        return self.config.get("bot_token", "")

    @property
    def chat_id(self) -> str:
        return str(self.config.get("chat_id", ""))

    def endpoint(self) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

    def render(self, result: AnalysisResult) -> dict:
        if result.report_markdown:
            text = result.report_markdown
            if len(text) > 3900:
                text = text[:3900] + "\n\n...(已截断)"
            return {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }
        return self._legacy_render(result)

    def render_error(self, result: AnalysisResult) -> dict:
        return {
            "chat_id": self.chat_id,
            "text": f"⚠️ {result.module_name or 'Megatron'} 分析输出异常\n\n请到 Web 后台查看运行 #{result.run_id} 的原始结果。\n\n错误: {result.parse_error[:100]}",
            "parse_mode": "Markdown",
        }

    def _legacy_render(self, result: AnalysisResult) -> dict:
        lines = [f"⚡ *{result.module_name or 'Megatron'}* 分析简报", ""]
        if result.briefing:
            lines.append(result.briefing)
            lines.append("")
        for i, it in enumerate(result.items or [], 1):
            title = it.get("title", "")
            summary = it.get("summary", "")
            url = it.get("source_url") or it.get("url", "")
            lines.append(f"{i}. *{title}*")
            if summary:
                lines.append(f"   {summary}")
            if url:
                lines.append(f"   [原文]({url})")
            lines.append("")
        return {
            "chat_id": self.chat_id,
            "text": "\n".join(lines)[:3900],
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
