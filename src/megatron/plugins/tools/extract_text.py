from __future__ import annotations

import httpx
import trafilatura

from .base import BaseTool, ToolResult, register_tool


@register_tool("extract_text")
class ExtractTextTool(BaseTool):
    """Fetch a URL and extract its main readable text (boilerplate-stripped).

    Complements fetch_url: use this when you want the *article body* of a blog
    post or advisory page, not raw HTML.
    """

    name = "extract_text"
    description = "获取指定 URL 并提取正文文本（自动去除导航/广告等噪音，适合博客文章、安全公告）。"

    def __init__(self, **config):
        super().__init__(**config)
        self.timeout = float(config.get("timeout", 20))
        self.max_chars = int(config.get("max_chars", 8000))

    @property
    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "要提取正文的 URL（通常是文章/公告页）",
                },
            },
            "required": ["url"],
        }

    async def run(self, url: str) -> ToolResult:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; MegatronBot/0.2)"},
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
            extracted = trafilatura.extract(
                resp.text,
                include_comments=False,
                include_tables=False,
                favor_precision=True,
            )
            if not extracted:
                return ToolResult(
                    name=self.name,
                    ok=False,
                    error="No readable text extracted (maybe JS-rendered page)",
                )
            return ToolResult(
                name=self.name,
                ok=True,
                data={
                    "url": url,
                    "text": extracted[: self.max_chars],
                    "length": len(extracted),
                },
            )
        except Exception as e:
            return ToolResult(name=self.name, ok=False, error=str(e))


__all__ = ["ExtractTextTool"]
