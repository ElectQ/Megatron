from __future__ import annotations

import base64
import hashlib
import hmac
import re
import urllib.parse

from ...core.logging import get_logger
from .base import AnalysisResult, BaseChannel, register_channel

logger = get_logger(__name__)

# 钉钉 markdown 单条上限约 5000 字符,留余量
MAX_CHARS_PER_MSG = 4500


@register_channel("dingtalk")
class DingTalkChannel(BaseChannel):
    """DingTalk (钉钉) custom robot webhook.

    Config:
        webhook_url (full URL with access_token)
        secret (optional, for sign-based security)

    Delivery:
    - Prefers report_markdown, sent as markdown msgtype.
    - Auto-splits into multiple messages when markdown exceeds 4500 chars.
    - Error notice when parse_error + no markdown.
    """

    kind = "dingtalk"

    def endpoint(self) -> str:
        url = self.config.get("webhook_url", "")
        secret = self.config.get("secret", "")
        if secret:
            url = _sign_url(url, secret)
        return url

    def render(self, result: AnalysisResult) -> dict:
        title, body = self._extract_title_body(result)
        if body:
            return {
                "msgtype": "markdown",
                "markdown": {
                    "title": title,
                    "text": f"# {title}\n\n{body}",
                },
            }
        return self._legacy_render(result, title)

    def _extract_title_body(self, result: AnalysisResult) -> tuple[str, str]:
        """Extract the LLM-generated title line from report_markdown."""
        md = result.report_markdown or ""
        if not md:
            return ("⚡ 安全简报", "")
        lines = md.split("\n", 1)
        first_line = lines[0].strip()
        # Use the first heading-style line from the LLM as the DingTalk title
        if first_line.startswith("⚡") or first_line.startswith("#"):
            return (first_line, lines[1].strip() if len(lines) > 1 else "")
        return ("⚡ 安全简报", md)

    def _legacy_render(self, result: AnalysisResult, title: str) -> dict:
        sections = []
        if result.briefing:
            sections.append(f"### 概述\n\n{result.briefing}\n")
        for i, it in enumerate(result.items or [], 1):
            t = it.get("title", "")
            s = it.get("summary", "")
            u = it.get("source_url") or it.get("url", "")
            line = f"{i}. **{t}**"
            if s:
                line += f"\n\n{s}"
            if u:
                line += f"\n\n[查看原文]({u})"
            sections.append(line)
        text = "\n\n---\n\n".join(sections)[:MAX_CHARS_PER_MSG]
        return {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": f"## {title}\n\n{text}"},
        }

    async def send(self, result: AnalysisResult) -> dict:
        """Override send to support multi-message splitting for long markdown."""
        try:
            # Error case
            if result.parse_error and not result.report_markdown:
                return await self._send_one(self.render_error(result))

            md = result.report_markdown
            if not md:
                # Legacy path (no markdown): single render
                return await self._send_one(self.render(result))

            # Split if needed
            chunks = self._split_markdown(md)
            total = len(chunks)
            if total <= 1:
                return await self._send_one(self.render(result))

            # Multi-message
            title_base, _ = self._extract_title_body(result)
            last_ok = True
            last_status = 0
            last_error = ""
            for i, chunk in enumerate(chunks, 1):
                payload = {
                    "msgtype": "markdown",
                    "markdown": {
                        "title": f"{title_base} ({i}/{total})",
                        "text": f"# {title_base} ({i}/{total})\n\n{chunk}",
                    },
                }
                r = await self._send_one(payload)
                if not r["ok"]:
                    last_ok = False
                    last_status = r["status_code"]
                    last_error = r["error"]
                    logger.warning(
                        "dingtalk.split.partial_fail",
                        chunk=i,
                        total=total,
                        error=last_error,
                    )
            return {"ok": last_ok, "status_code": last_status, "error": last_error}
        except Exception as e:
            logger.error("channel.send_failed", kind=self.kind, error=str(e))
            return {"ok": False, "status_code": 0, "error": str(e)[:200]}

    async def _send_one(self, payload: dict) -> dict:
        from httpx import AsyncClient

        url = self.endpoint()
        async with AsyncClient(timeout=15) as client:
            resp = await client.post(
                url, json=payload, headers={"Content-Type": "application/json"}
            )
        if resp.status_code < 300:
            logger.info("channel.sent", kind=self.kind, url=url[:60], status=resp.status_code)
            return {"ok": True, "status_code": resp.status_code, "error": ""}
        return {"ok": False, "status_code": resp.status_code, "error": resp.text[:200]}

    def _split_markdown(self, md: str) -> list[str]:
        """Split markdown into chunks under MAX_CHARS_PER_MSG.

        Strategy: split on ## headings, accumulate into chunks.
        """
        if len(md) <= MAX_CHARS_PER_MSG:
            return [md]

        sections = re.split(r"(?=^## )", md, flags=re.MULTILINE)
        sections = [s for s in sections if s.strip()]

        chunks: list[str] = []
        current = ""
        for section in sections:
            if len(current) + len(section) > MAX_CHARS_PER_MSG and current:
                chunks.append(current)
                current = section
            else:
                current += section
        if current:
            chunks.append(current)

        # If a single section exceeds limit, hard-split it
        final: list[str] = []
        for chunk in chunks:
            if len(chunk) <= MAX_CHARS_PER_MSG:
                final.append(chunk)
            else:
                for i in range(0, len(chunk), MAX_CHARS_PER_MSG):
                    final.append(chunk[i : i + MAX_CHARS_PER_MSG])
        return final


def _sign_url(url: str, secret: str) -> str:
    import time as _time

    timestamp = str(round(_time.time() * 1000))
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}timestamp={timestamp}&sign={sign}"


__all__ = ["DingTalkChannel"]
