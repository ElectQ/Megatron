from __future__ import annotations

import httpx

from .base import BaseTool, ToolResult, register_tool


@register_tool("fetch_url")
class FetchUrlTool(BaseTool):
    """Fetch a URL and return raw body (truncated). The LLM decides what to
    fetch — this tool is a generic capability, not tied to any analysis."""

    name = "fetch_url"
    description = (
        "获取指定 URL 的网页原始内容（文本/HTML，截断到 6000 字符）。用于读取推文中提到的链接详情。"
    )

    def __init__(self, **config):
        super().__init__(**config)
        self.timeout = float(config.get("timeout", 20))
        self.max_chars = int(config.get("max_chars", 6000))
        self._headers = {
            "User-Agent": config.get(
                "user_agent",
                "Mozilla/5.0 (compatible; MegatronBot/0.2; +https://megatron.local)",
            )
        }

    @property
    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "要获取的完整 URL",
                },
            },
            "required": ["url"],
        }

    async def run(self, url: str) -> ToolResult:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, follow_redirects=True, headers=self._headers
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")
                text = resp.text[: self.max_chars]
                return ToolResult(
                    name=self.name,
                    ok=True,
                    data={
                        "url": str(resp.url),
                        "status": resp.status_code,
                        "content_type": content_type,
                        "text": text,
                    },
                )
        except httpx.HTTPStatusError as e:
            return ToolResult(name=self.name, ok=False, error=f"HTTP {e.response.status_code}")
        except Exception as e:
            return ToolResult(name=self.name, ok=False, error=str(e))


__all__ = ["FetchUrlTool"]
