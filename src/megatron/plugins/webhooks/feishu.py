from __future__ import annotations

from .base import AnalysisResult, BaseChannel, register_channel


@register_channel("feishu")
class FeishuChannel(BaseChannel):
    """Feishu (Lark) custom bot webhook.

    Config: webhook_url
    Delivery: prefers report_markdown (rendered as lark_md interactive card).
    """

    kind = "feishu"

    def endpoint(self) -> str:
        return self.config.get("webhook_url", "")

    def render(self, result: AnalysisResult) -> dict:
        if result.report_markdown:
            elements = [
                {
                    "tag": "div",
                    "text": {"content": result.report_markdown[:28000], "tag": "lark_md"},
                }
            ]
            header = {
                "title": {
                    "tag": "plain_text",
                    "content": f"⚡ {result.module_name or 'Megatron'} 安全简报",
                },
                "template": "blue",
            }
            return {"msg_type": "interactive", "card": {"header": header, "elements": elements}}
        return self._legacy_render(result)

    def _legacy_render(self, result: AnalysisResult) -> dict:
        elements = []
        if result.briefing:
            elements.append({"tag": "div", "text": {"content": result.briefing, "tag": "lark_md"}})
        for it in (result.items or [])[:20]:
            title = it.get("title", "")
            summary = it.get("summary", "")
            url = it.get("source_url") or it.get("url", "")
            content = f"**{title}**"
            if summary:
                content += f"\n{summary}"
            elements.append({"tag": "div", "text": {"content": content, "tag": "lark_md"}})
            if url:
                elements.append(
                    {
                        "tag": "action",
                        "actions": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "查看原文"},
                                "url": url,
                                "type": "default",
                            }
                        ],
                    }
                )
        header = {
            "title": {
                "tag": "plain_text",
                "content": f"⚡ {result.module_name or 'Megatron'} 分析简报",
            },
            "template": "blue",
        }
        return {"msg_type": "interactive", "card": {"header": header, "elements": elements}}
