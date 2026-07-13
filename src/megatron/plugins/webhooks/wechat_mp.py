"""WeChat Official Account (微信公众号) — 群发 to all followers.

Unlike a group-robot webhook (钉钉/企业微信/飞书, which is a single POST to a URL),
the 公众号 is a two-step, credentialed flow:

    AppID + AppSecret  ──GET /cgi-bin/token──▶  access_token (account-global, 7200s)
    access_token       ──POST message/mass/sendall──▶  broadcast to all followers

So this channel overrides ``send`` entirely. Two things bite the unwary:

* The WeChat API returns **HTTP 200 even on errors** — success is ``errcode == 0``,
  not the status code. We parse the body.
* ``send`` here 群发s to **every** follower and, on a 订阅号, burns the 1/day quota.
  ``test`` therefore only fetches a token (proves the credentials + IP whitelist)
  and never actually broadcasts.

Operational prerequisites (a 群发 will fail without them):
  1. 已认证公众号 — 群发接口是认证接口;个人未认证订阅号调用会 48001 api unauthorized。
  2. IP 白名单 — 服务器公网 IP 必须在「开发 → 基本配置 → IP白名单」,否则 token 获取被拒(40164)。
  3. 频率 — 认证订阅号 1 条/天;服务号 4 条/月。
"""

from __future__ import annotations

import re
import time

import httpx

from ...core.logging import get_logger
from .base import AnalysisResult, BaseChannel, register_channel

logger = get_logger(__name__)

_API = "https://api.weixin.qq.com"
# 群发文本上限约 2048 字符;留点余量,超出裁掉(digest 已带日刊链接兜底)。
_MAX_TEXT = 2000
# Refresh a little before the stated 7200s expiry to avoid using a token that
# expires mid-request.
_TOKEN_SLACK = 200

# access_token is per-AppID and account-global (fetching a new one invalidates
# the old), so it is cached across channel instances / dispatches, not per-send.
_token_cache: dict[str, tuple[str, float]] = {}


def _md_to_text(md: str) -> str:
    """Flatten Markdown to the plain text 群发 shows (it renders no Markdown).

    Links become "label url" — WeChat auto-links bare URLs in the client, so the
    原文 link stays tappable. Bold/heading markers are stripped so they don't show
    as literal ``**`` / ``#``.
    """
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 \2", md or "")
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"^\s*[-*]\s+", "· ", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


@register_channel("wechat_mp")
class WeChatMPChannel(BaseChannel):
    """微信公众号 群发渠道。Config: appid, appsecret."""

    kind = "wechat_mp"

    def endpoint(self) -> str:  # unused (send is overridden); satisfies the ABC
        return f"{_API}/cgi-bin/message/mass/sendall"

    def render(self, result: AnalysisResult) -> dict:
        return {
            "filter": {"is_to_all": True},
            "msgtype": "text",
            "text": {"content": self._content(result)},
        }

    def _content(self, result: AnalysisResult) -> str:
        md = result.report_markdown or result.briefing or ""
        return _md_to_text(md)[:_MAX_TEXT] or "（本次无内容）"

    def render_error(self, result: AnalysisResult) -> dict:
        return {
            "filter": {"is_to_all": True},
            "msgtype": "text",
            "text": {
                "content": (
                    f"⚠️ {result.module_name or 'Megatron'} 分析输出异常，"
                    f"请到后台查看运行 #{result.run_id}。"
                )
            },
        }

    async def _access_token(self, client: httpx.AsyncClient) -> str:
        appid = self.config.get("appid", "")
        secret = self.config.get("appsecret", "")
        if not appid or not secret:
            raise RuntimeError("appid/appsecret 未配置")
        cached = _token_cache.get(appid)
        if cached and cached[1] > time.time():
            return cached[0]
        r = await client.get(
            f"{_API}/cgi-bin/token",
            params={"grant_type": "client_credential", "appid": appid, "secret": secret},
        )
        data = r.json()
        token = data.get("access_token")
        if not token:
            # 40164 IP 不在白名单 · 40125 secret 错 · 48001 未认证/无权限
            raise RuntimeError(
                f"获取 access_token 失败: {data.get('errcode')} {data.get('errmsg')}"
            )
        ttl = int(data.get("expires_in", 7200)) - _TOKEN_SLACK
        _token_cache[appid] = (token, time.time() + max(ttl, 0))
        return token

    async def send(self, result: AnalysisResult) -> dict:
        try:
            payload = (
                self.render_error(result)
                if result.parse_error and not result.report_markdown
                else self.render(result)
            )
            async with httpx.AsyncClient(timeout=15) as client:
                token = await self._access_token(client)
                r = await client.post(
                    f"{_API}/cgi-bin/message/mass/sendall",
                    params={"access_token": token},
                    json=payload,
                )
            data = r.json()
            errcode = data.get("errcode", -1)
            if errcode == 0:
                logger.info("channel.sent", kind=self.kind, msg_id=data.get("msg_id"))
                return {"ok": True, "status_code": 200, "error": ""}
            # token expired/invalid mid-flight → drop cache so the next run refetches
            if errcode in (40001, 40014, 42001):
                _token_cache.pop(self.config.get("appid", ""), None)
            logger.error(
                "channel.send_failed", kind=self.kind, errcode=errcode, errmsg=data.get("errmsg")
            )
            return {
                "ok": False,
                "status_code": 200,
                "error": f"{errcode} {data.get('errmsg')}"[:200],
            }
        except Exception as e:
            logger.error("channel.send_failed", kind=self.kind, error=str(e))
            return {"ok": False, "status_code": 0, "error": str(e)[:200]}

    async def test(self) -> dict:
        """Verify credentials WITHOUT broadcasting.

        A real 群发 would hit every follower and consume the daily quota, so a
        connection test just proves AppID/AppSecret + IP whitelist by fetching a
        token — it never sends.
        """
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                await self._access_token(client)
            return {
                "ok": True,
                "status_code": 200,
                "error": "凭据有效,access_token 获取成功(测试不会真的群发)",
            }
        except Exception as e:
            return {"ok": False, "status_code": 0, "error": str(e)[:200]}


__all__ = ["WeChatMPChannel"]
