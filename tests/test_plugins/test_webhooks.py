from __future__ import annotations


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
