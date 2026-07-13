"""WeChat Official Account (公众号) 群发 channel.

Drives the two-step credentialed flow with a fake httpx client — no network.
Covers: token→sendall happy path, WeChat's HTTP-200-with-errcode failures, the
credentials-only `test` (must not broadcast), and Markdown→text flattening.
"""

from __future__ import annotations

import pytest

from megatron.plugins.webhooks import wechat_mp
from megatron.plugins.webhooks.base import AnalysisResult, channel_registry


class _Resp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class _FakeClient:
    def __init__(self, token_data, send_data, calls):
        self._token, self._send, self._calls = token_data, send_data, calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        self._calls.append(("get", url, params))
        return _Resp(self._token)

    async def post(self, url, params=None, json=None):
        self._calls.append(("post", url, params, json))
        return _Resp(self._send)


@pytest.fixture(autouse=True)
def _clear_token_cache():
    wechat_mp._token_cache.clear()
    yield
    wechat_mp._token_cache.clear()


def _patch(monkeypatch, token_data, send_data, calls):
    monkeypatch.setattr(
        wechat_mp.httpx,
        "AsyncClient",
        lambda **kw: _FakeClient(token_data, send_data, calls),
    )


def _result():
    return AnalysisResult(
        briefing="",
        items=[],
        raw={},
        run_id=7,
        module_name="推特安全流",
        report_markdown="⚡ 推特安全流 · 07-13\n\n🔴 **必看**\n1. **某洞** [原文 ↗](https://x.com/a)",
    )


def test_registered():
    assert "wechat_mp" in channel_registry.names()


@pytest.mark.asyncio
async def test_send_success_broadcasts_flattened_text(monkeypatch):
    calls: list = []
    _patch(monkeypatch, {"access_token": "TOK", "expires_in": 7200}, {"errcode": 0, "msg_id": 1}, calls)
    ch = wechat_mp.WeChatMPChannel(appid="wx1", appsecret="sec")

    out = await ch.send(_result())

    assert out["ok"] is True
    # token first, then the sendall POST
    assert calls[0][0] == "get"
    post = next(c for c in calls if c[0] == "post")
    body = post[3]
    assert body["filter"] == {"is_to_all": True}
    assert body["msgtype"] == "text"
    content = body["text"]["content"]
    assert "**" not in content and "[原文" not in content  # markdown stripped
    assert "https://x.com/a" in content  # link kept as bare (tappable) URL


@pytest.mark.asyncio
async def test_token_failure_surfaces_errcode(monkeypatch):
    # e.g. 40164: server IP not on the account whitelist
    calls: list = []
    _patch(monkeypatch, {"errcode": 40164, "errmsg": "invalid ip"}, {"errcode": 0}, calls)
    ch = wechat_mp.WeChatMPChannel(appid="wx1", appsecret="sec")

    out = await ch.send(_result())

    assert out["ok"] is False
    assert "40164" in out["error"]
    # never reached the sendall POST
    assert all(c[0] != "post" for c in calls)


@pytest.mark.asyncio
async def test_send_errcode_is_failure_despite_http_200(monkeypatch):
    # 48001: unauthenticated account has no 群发 permission — WeChat still 200s.
    calls: list = []
    _patch(monkeypatch, {"access_token": "TOK", "expires_in": 7200}, {"errcode": 48001, "errmsg": "api unauthorized"}, calls)
    ch = wechat_mp.WeChatMPChannel(appid="wx1", appsecret="sec")

    out = await ch.send(_result())

    assert out["ok"] is False
    assert "48001" in out["error"]


@pytest.mark.asyncio
async def test_test_only_fetches_token_never_broadcasts(monkeypatch):
    calls: list = []
    _patch(monkeypatch, {"access_token": "TOK", "expires_in": 7200}, {"errcode": 0}, calls)
    ch = wechat_mp.WeChatMPChannel(appid="wx1", appsecret="sec")

    out = await ch.test()

    assert out["ok"] is True
    assert any(c[0] == "get" for c in calls)
    assert all(c[0] != "post" for c in calls)  # no 群发 during a test


@pytest.mark.asyncio
async def test_token_is_cached_across_sends(monkeypatch):
    calls: list = []
    _patch(monkeypatch, {"access_token": "TOK", "expires_in": 7200}, {"errcode": 0}, calls)
    ch = wechat_mp.WeChatMPChannel(appid="wx1", appsecret="sec")

    await ch.send(_result())
    await ch.send(_result())

    # token fetched once, reused for the second send
    assert sum(1 for c in calls if c[0] == "get") == 1


@pytest.mark.asyncio
async def test_missing_credentials_fail_clean(monkeypatch):
    calls: list = []
    _patch(monkeypatch, {}, {}, calls)
    ch = wechat_mp.WeChatMPChannel(appid="", appsecret="")
    out = await ch.send(_result())
    assert out["ok"] is False
    assert not calls  # never hit the network


def test_md_to_text_flattens():
    t = wechat_mp._md_to_text("# 标题\n\n**粗** [看这里](https://e.com/x)\n- 项目")
    assert "#" not in t and "**" not in t
    assert "看这里 https://e.com/x" in t
    assert "· 项目" in t
