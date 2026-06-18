from __future__ import annotations

import pytest

from megatron.plugins.tools.base import ToolResult, ToolSet, tool_registry


def test_tools_registered():
    assert "fetch_url" in tool_registry
    assert "extract_text" in tool_registry
    assert "lookup_cve" in tool_registry


def test_toolset_from_config_skips_disabled_and_unknown():
    ts = ToolSet.from_config(
        [
            {"name": "fetch_url", "enabled": True},
            {"name": "extract_text", "enabled": False},
            {"name": "nonexistent"},
        ]
    )
    assert ts.names == ["fetch_url"]
    specs = ts.function_specs()
    assert len(specs) == 1
    assert specs[0]["function"]["name"] == "fetch_url"


def test_toolset_empty_config():
    ts = ToolSet.from_config([])
    assert len(ts) == 0
    assert ts.function_specs() == []


@pytest.mark.asyncio
async def test_toolset_execute_unknown_returns_error():
    ts = ToolSet.from_config([])
    result = await ts.execute("fetch_url", {"url": "http://x"})
    assert not result.ok
    assert "not enabled" in result.error


def test_tool_result_serialization():
    ok = ToolResult(name="t", ok=True, data={"key": "value"})
    assert "value" in ok.to_llm_message()

    err = ToolResult(name="t", ok=False, error="boom")
    assert "[ERROR] boom" in err.to_llm_message()


def test_fetch_url_schema():
    tool = tool_registry.create("fetch_url")
    spec = tool.function_spec()
    assert spec["function"]["name"] == "fetch_url"
    assert "url" in spec["function"]["parameters"]["properties"]


def test_lookup_cve_schema():
    tool = tool_registry.create("lookup_cve")
    spec = tool.function_spec()
    assert "cve_id" in spec["function"]["parameters"]["properties"]
