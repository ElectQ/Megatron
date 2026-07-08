"""Lightweight UI internationalization.

English is the source language: the English string is itself the lookup key, so
any string not present in a locale table falls back to English automatically.
The active locale is carried in the ``lang`` cookie (default ``en``). Templates
call ``t("English text")``; ``_render`` binds ``t`` to the request's locale.
"""

from __future__ import annotations

# Display names for the language switcher. Order = switcher order.
SUPPORTED_LANGS: dict[str, str] = {
    "en": "English",
    "zh": "简体中文",
}
DEFAULT_LANG = "en"

# Simplified-Chinese overrides, keyed by the English source string.
# Only strings that appear (wrapped in t()) in templates need an entry here;
# anything missing renders as its English key.
_ZH: dict[str, str] = {
    # Brand / chrome
    "LLM Analysis Hub": "LLM 分析中心",
    "online": "在线",
    "Guest": "访客",
    "Sign out": "退出登录",
    "Language": "语言",
    # Nav sections
    "Data": "数据",
    "Analysis": "分析",
    "Configuration": "配置",
    # Nav items
    "Overview": "概览",
    "Sources": "数据源",
    "Collected": "已采集",
    "Analyzed": "已分析",
    "Tasks": "任务",
    "Schedules": "定时",
    "Prompts": "提示词",
    "Models": "模型",
    "Delivery": "推送",
    # Common actions
    "Run a task": "运行任务",
    "View all": "查看全部",
    "Filter": "筛选",
    "Clear": "清除",
    "Add": "添加",
    "Save": "保存",
    "Save changes": "保存修改",
    "Create task": "创建任务",
    "Cancel": "取消",
    "Preview": "预览",
    "Delete": "删除",
    "Test": "测试",
    "Test connection": "测试连接",
    "Discover": "发现",
    "Enabled": "启用",
    "Add channel": "添加通道",
    "Add provider": "添加模型",
    "Add MCP server": "添加 MCP 服务器",
    "Connect MCP Server": "连接 MCP 服务器",
    # Dashboard / overview
    "Pipeline health": "流水线状态",
    "today": "今日",
    "last": "最近",
    "never": "从未",
    "success": "成功",
    "failed": "失败",
    "Collected items": "已采集条目",
    "Today's execution and cost — resets at UTC 00:00.": "今日执行与花费 — 于 UTC 00:00 重置。",
    "channels configured": "个已配置通道",
    "manage": "管理",
    "Recent runs": "最近运行",
    "7-day trend": "近 7 天趋势",
    "Per-task breakdown": "各任务明细",
    "Runs": "运行数",
    "Success rate": "成功率",
    "Tokens": "Tokens",
    "Cost": "花费",
    "New items": "新增条目",
    "Duration": "耗时",
    "collected today": "今日采集",
    # Page titles + subtitles
    "Collected Data": "已采集数据",
    "Raw items ingested from configured sources.": "从已配置数据源摄取的原始条目。",
    "Analyzed Data": "已分析数据",
    "Structured outputs and briefings produced by analysis tasks.": "分析任务产出的结构化结果与简报。",
    "Run History": "运行历史",
    "Past analysis executions and their outputs.": "过往的分析执行记录及其产出。",
    "Active cron schedules and module dispatch status.": "生效中的 cron 定时及任务派发状态。",
    "Analysis modules combine a source, LLM, prompt, tools, and webhooks.": "分析任务将数据源、LLM、提示词、工具与推送组合在一起。",
    # Tasks page
    "New task": "新建任务",
    "Edit task": "编辑任务",
    "Your tasks": "任务列表",
    "Task": "任务",
    "Source": "数据源",
    "Model": "模型",
    "Prompt": "提示词",
    "Agent": "Agent",
    "Schedule": "定时",
    "Status": "状态",
    "Actions": "操作",
    "manual": "手动",
    "last run": "上次",
    "runs": "次",
    "enabled": "已启用",
    "disabled": "已停用",
    "Run": "运行",
    "Edit": "编辑",
    "History": "历史",
    "No tasks yet. Create one below to start analyzing collected data.":
        "还没有任务。在下方新建一个,开始分析已采集的数据。",
    "A task = source + LLM + prompt + optional tools + optional delivery + schedule.":
        "一个任务 = 数据源 + LLM + 提示词 +(可选)工具 +(可选)推送 + 执行方式。",
    "Basic info": "基本信息",
    "Task name": "任务名称",
    "Description": "描述",
    "optional": "可选",
    "Data source": "数据源",
    "Source type": "源类型",
    "Source reference": "源引用",
    "Time window": "时间范围",
    "Specific date": "指定日期",
    "Date range": "日期区间",
    "Rolling window": "滚动窗口",
    "Max items": "最大条数",
    "unlimited": "不限",
    "Interaction filtering is delegated to the LLM value judgment.":
        "互动量筛选交由 LLM 价值判断处理。",
    "No models yet — add one under Models first.": "还没有模型 —— 请先在「模型」中添加。",
    "No prompts yet — create one under Prompts first.": "还没有提示词 —— 请先在「提示词」中创建。",
    "Agent mode": "Agent 模式",
    "Agent backend": "Agent 后端",
    "Enabled tools": "启用的工具",
    "No channels configured. Results will be stored without delivery.":
        "尚未配置通道。结果将仅保存,不推送。",
    "Execution mode": "执行方式",
    "Manual only": "仅手动",
    "Scheduled": "定时执行",
    "Cron expression (UTC)": "Cron 表达式(UTC)",
    "daily": "每天",
    "weekly": "每周",
    "every 6h": "每 6 小时",
    "Sources subtitle": "所有数据源均通过 MCP 协议接入。添加一个 MCP 服务器即可开始采集数据。",
    "All data sources connect via the MCP protocol. Add an MCP server to start ingesting data.":
        "所有数据源均通过 MCP 协议接入。添加一个 MCP 服务器即可开始采集数据。",
    "Delivery subtitle": "接收分析结果的 Webhook 通道。",
    "Webhook channels that receive analysis results.": "接收分析结果的 Webhook 通道。",
    "Models subtitle": "分析任务使用的 LLM 提供方与 API 密钥。",
    "LLM providers and API keys used by analysis tasks.": "分析任务使用的 LLM 提供方与 API 密钥。",
    "Jinja2 templates used by analysis tasks.": "分析任务使用的 Jinja2 模板。",
    # Card titles
    "New template": "新建模板",
    "Add MCP Server": "添加 MCP 服务器",
    # Login
    "Sign in": "登录",
    "Username": "用户名",
    "Password": "密码",
    "Invalid username or password": "用户名或密码错误",
}

_TABLES: dict[str, dict[str, str]] = {"zh": _ZH}


def normalize_lang(code: str | None) -> str:
    """Return a supported language code, defaulting to English."""
    if code and code in SUPPORTED_LANGS:
        return code
    return DEFAULT_LANG


def get_lang(request) -> str:
    """Resolve the active locale from the ``lang`` cookie."""
    return normalize_lang(request.cookies.get("lang") if request else None)


def make_translator(lang: str):
    """Return a ``t(text)`` callable bound to ``lang``."""
    table = _TABLES.get(lang, {})

    def t(text: str) -> str:
        return table.get(text, text)

    return t
