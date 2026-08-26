from __future__ import annotations

import asyncio

from megatron.plugins.webhooks.base import AnalysisResult, channel_registry


def _make_result():
    return AnalysisResult(
        briefing="今日发现 2 个漏洞",
        items=[
            {
                "title": "Apache RCE",
                "cve": "CVE-2024-9999",
                "severity": "high",
                "category": "vuln",
                "summary": "严重",
                "source_url": "http://x",
                "url": "http://x",
            },
            {
                "title": "Patch",
                "cve": "",
                "severity": "low",
                "category": "advisory",
                "summary": "小补丁",
                "source_url": "http://y",
                "url": "http://y",
            },
        ],
        report_markdown="# ⚡ 简报\n\n## 🟥 漏洞\n**[CVE-2024-9999] Apache RCE**\n严重\n> [原文](http://x)",
        raw={},
        run_id=1,
        module_name="test",
    )


def test_channels_registered():
    for k in ("telegram", "feishu", "wecom", "dingtalk"):
        assert k in channel_registry


def test_telegram_render_prefers_markdown():
    ch = channel_registry.create("telegram", bot_token="x", chat_id="123")
    payload = ch.render(_make_result())
    assert payload["chat_id"] == "123"
    assert "CVE-2024-9999" in payload["text"]
    assert "Apache RCE" in payload["text"]
    assert payload["parse_mode"] == "Markdown"


def test_telegram_legacy_render_without_markdown():
    ch = channel_registry.create("telegram", bot_token="x", chat_id="123")
    legacy = AnalysisResult(
        briefing="概述",
        items=[{"title": "X", "summary": "s", "source_url": "http://x"}],
        raw={},
        run_id=1,
        report_markdown="",
    )
    payload = ch.render(legacy)
    assert "X" in payload["text"]
    assert payload["chat_id"] == "123"


def test_feishu_render():
    ch = channel_registry.create("feishu", webhook_url="http://feishu/hook")
    payload = ch.render(_make_result())
    assert payload["msg_type"] == "interactive"
    elements = payload["card"]["elements"]
    assert any("Apache RCE" in e["text"]["content"] for e in elements)


def test_wecom_render():
    ch = channel_registry.create("wecom", webhook_url="http://wecom/hook")
    payload = ch.render(_make_result())
    assert payload["msgtype"] == "markdown"
    assert "Apache RCE" in payload["markdown"]["content"]


def test_split_markdown_bytes():
    from megatron.plugins.webhooks.base import split_markdown_bytes

    # Fits: unchanged
    assert split_markdown_bytes("hello", 100) == ["hello"]
    assert split_markdown_bytes("", 100) == []

    # Long text splits on line boundaries, every chunk under the byte cap
    long_md = "\n".join(f"{i}. " + "好" * 40 for i in range(1, 30))  # ~3600+ bytes
    chunks = split_markdown_bytes(long_md, 400)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.encode("utf-8")) <= 400
    assert "".join(chunks).replace("\n", "") == long_md.replace("\n", "")

    # A single overlong line is hard-split without losing content
    line = "x" * 1000
    hard = split_markdown_bytes(line, 300)
    assert all(len(c.encode("utf-8")) <= 300 for c in hard)
    assert "".join(hard) == line


def test_wecom_send_splits_long_markdown(monkeypatch):
    from megatron.plugins.webhooks.wecom import MAX_BYTES

    sent = []

    class FakeResp:
        status_code = 200
        text = ""

    async def fake_post(self, url, json=None, headers=None):
        sent.append(json)
        return FakeResp()

    ch = channel_registry.create("wecom", webhook_url="http://wecom/hook")
    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    long_md = "\n".join(f"{i}. **标题{i}**" + "内容" * 60 for i in range(1, 30))
    assert len(long_md.encode("utf-8")) > MAX_BYTES
    result = AnalysisResult(
        briefing="",
        items=[],
        raw={},
        run_id=1,
        module_name="test",
        report_markdown=long_md,
    )
    outcome = asyncio.run(ch.send(result))

    assert outcome["ok"] is True
    assert len(sent) > 1, "long markdown should split into multiple messages"
    for payload in sent:
        content = payload["markdown"]["content"]
        assert len(content.encode("utf-8")) <= MAX_BYTES + 200  # marker headroom
    # Nothing lost: concatenated content contains every item
    joined = "".join(p["markdown"]["content"] for p in sent)
    for i in range(1, 30):
        assert f"{i}. **标题{i}**" in joined


def test_dingtalk_render():
    ch = channel_registry.create("dingtalk", webhook_url="http://dt/hook")
    payload = ch.render(_make_result())
    assert payload["msgtype"] == "markdown"
    assert "Apache RCE" in payload["markdown"]["text"]


def test_dingtalk_sign_url():
    from megatron.plugins.webhooks.dingtalk import _sign_url

    url = _sign_url("http://dt/hook?access_token=tok", "SEC123")
    assert "timestamp=" in url
    assert "sign=" in url


def test_channel_endpoints():
    assert (
        channel_registry.create("telegram", bot_token="b", chat_id="c").endpoint()
        == "https://api.telegram.org/botb/sendMessage"
    )
    assert channel_registry.create("feishu", webhook_url="https://x").endpoint() == "https://x"
    assert channel_registry.create("wecom", webhook_url="https://y").endpoint() == "https://y"
    assert channel_registry.create("dingtalk", webhook_url="https://z").endpoint() == "https://z"
