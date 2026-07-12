"""Bootstrap: auto-configure Megatron on first boot.

Reads config from environment variables and seeds the database.
Idempotent — safe to run on every startup.
"""

from __future__ import annotations

import secrets
import os

from sqlalchemy import select

from .logging import get_logger

logger = get_logger(__name__)

DEFAULT_PROMPT_NAME = "daily_security_briefing"
DEFAULT_PROMPT_DISPLAY = "推特安全信息流简报"
DEFAULT_PROMPT_TEMPLATE = """\
你是一名信息安全情报分析师。下面是采集到的 {{ item_count }} 条推文（来自安全研究者/从业者）：

{% for t in top_items %}
[{{ loop.index }}] @{{ t.author }}
{{ t.content[:400] }}
{% if t.links %}文中提到的链接: {{ t.links | join(', ') }}{% endif %}
原文: {{ t.url }}
{% endfor %}

# 你的任务

## 第一步：筛选（边界从宽，宁多勿漏）
只剔除**明确非网安**的内容。网安相关和边界内容都收。

**算网安（必收）**：
- 漏洞/利用/PoC/绕过技术
- 安全工具（红队/逆向/调试/分析/攻击/防御）
- 攻击技术、防御研究、威胁情报
- CVE 分析、安全事件、官方公告
- 安全教程/系列、CTF/writeup

**边界但收**：
- 开发工具（可被用于攻击的终端/编译器/调试器）
- 系统编程（汇编/底层/内核）
- AI 在安全的应用、密码学研究
- 安全会议/CFP/资源合集

**不收**：
- 纯个人日常、政治、招聘、营销
- 纯情绪/吐槽（无技术内容）

## 第二步：去重合并
同一事件/工具/漏洞如果多人发了，合并成一条，保留最具信息量的描述。

## 第三步：freshness 标注
对每条标注 freshness：
- `new`：今日首发的新成果（新工具/新文章/新研究）
- `reshare`：今日重新讨论/分享的旧成果（仍是情报价值）

注意：即使是旧文章/旧工具，只要今天有人在安全圈讨论/重新分享，就要收录，标 `reshare`。

## 第四步：分档（核心 — 优先级原则）

**优先级：研究价值 + 实战价值 > 互动热度。已有成果 > 讨论/预告/感叹。**

### 🔴 必看 (tier: must)
**判定：已有成果（不论互动多少，不论 new/reshare）**
- 已发布可用工具（GitHub repo + 文档）
- 完整技术文章/研究（含原理 + 实现/分析）
- CVE/漏洞分析（含 PoC / 影响范围 / 绕过细节）
- 突破性技术成果（逆向/协议破解/新型攻击）

### 🟡 推荐 (tier: recommended)
**判定：有价值但未完整**
- 有外部链接但需进一步了解
- 有实质内容的技术讨论
- 工具更新/版本发布
- 攻击/防御技术分析片段
- 教程系列/资源（非首发）

### ⚪ 速览 (tier: quick)
**判定：行业动态/资源，无技术成果**
- 人事变动/行业新闻
- 会议 CFP/活动通知
- 预告/梗/观点/评论
- 无实质技术内容的讨论

### 反例（严格遵守）
- ❌ 互动 200 但只是"我离职了"/"感谢XX" → **速览**
- ❌ "big things coming soon" 预告无实质 → **速览或不收**
- ✅ 互动 5 但有完整 GitHub 仓库 + 技术文档 → **必看**
- ✅ 2020 年旧文但今天重新讨论 + 有 repo → **必看**(reshare)

### 数量自适应
- 当天网安内容少 → 多进必看
- 当天内容多 → 必看 3-7 + 推荐 8-15 + 速览其余

## 第五步：深挖（如果启用了工具）
对必看档的条目，如果推文**带有外部链接**（GitHub 仓库、博客、Advisory、技术文章等），用 `fetch_url` 或 `extract_text` 工具去读取内容，补充关键细节（工具用途、漏洞影响、技术原理）。

## 第六步：双格式输出

输出一个 JSON，包含两个字段：

### 字段一：report_markdown（分档简报，用于推送）

按以下结构写 Markdown 简报（中文，手机扫读友好）：

```
⚡ 推特安全信息流简报 · {{ now[:10] }}
共 N 条 · 必看 X · 推荐 Y · 速览 Z

🔴 必看 X 条

1. 标题 🆕（或 ♻️）
   • 要点 1（一行一个，简洁）
   • 要点 2
   • 要点 3
   @author · [原文] · [相关链接]

2. 标题 ♻️
   • 要点
   • 要点
   @author · [原文] · [repo]

🟡 推荐 Y 条

1. **标题**
   30-60字中文技术解读（含攻击手法/漏洞影响范围/关键发现/工具用途，简洁准确）
   [原文](链接)

2. **标题**
   30-60字中文技术解读
   [原文](链接)

⚪ 速览 Z 条

- 简短标题（限15字以内） [链接]
- 简短标题 [链接]
- 简短标题 [链接]
```

**格式规则**：
- 标题用日期格式：`⚡ 推特安全信息流简报 · {{ now[:10] }}`（日期必须用变量 {{ now[:10] }} 替换）
- 第二行是统计行：`共 N 条 · 必看 X · 推荐 Y · 速览 Z`
- **不要顶部概述/趋势段**，直接进必看
- 必看档：每条用 bullet（•）列 2-5 个要点（数量自适应，简单内容少写、复杂的多写），后面跟 `@author · [链接]`
- 必看档标题后加 `🆕`（new）或 `♻️`（reshare）标注
- 推荐档：每条编号，粗体标题，下面缩进一行 30-60 字技术解读，再一行链接
- 速览档：每条单独一行，`- 标题 [链接]`（用 `- ` 而非 `•`，确保钉钉正确渲染为列表），标题限制15字内
- 某档为空就不写该区块
- 控制总长度在 5000 字符内（手机扫读友好，不要无意义凑字数）

### 字段二：items（机器用的全量结构化，存 Web/DB）

**所有通过筛选的网安条目都要进 items（不限数量，哪怕 30-50 条都收）**。每条：
```
{
  "title": "简短中文标题",
  "category": "vuln | tool | research | threat | incident | advisory",
  "tier": "must | recommended | quick",
  "freshness": "new | reshare",
  "cve": "CVE编号，没有则留空",
  "summary": "30-60字中文解读",
  "links": ["相关参考链接（原文+深挖到的）"],
  "source_url": "推文原文链接",
  "author": "@作者",
  "artifact": "repo | article | poc | video | none"
}
```

**artifact 字段说明**（成果类型）：
- repo: 有 GitHub/GitLab 仓库
- article: 有完整技术文章/博客
- poc: 有 PoC/演示视频
- video: 有视频内容
- none: 无具体成果（讨论/预告/观点）

# 最终输出格式（严格 JSON，不要任何额外文字）

{
  "report_markdown": "⚡ 推特安全信息流简报 · {{ now[:10] }}\\n共 N 条 · ...\\n\\n🔴 必看...",
  "items": [
    {"title":"...","category":"tool","tier":"must","freshness":"new","cve":"","summary":"...","links":["..."],"source_url":"...","author":"@...","artifact":"repo"}
  ]
}

# category 含义
- vuln: 漏洞披露/分析
- tool: 新工具/开源项目
- research: 技术研究/文章/Paper
- threat: 威胁情报/IOC/攻击活动
- incident: 安全事件/数据泄露
- advisory: 官方公告/补丁

# 关键注意
- items 包含所有通过筛选的网安条目（必看+推荐+速览全量，不限数量）
- report_markdown 是精简版，用于推送（不必把 items 所有内容都展开）
- 每条必须带 source_url（推文原文）
- report_markdown 里的 \\n 是换行，要正确转义

# 重要格式提醒（严格遵守）
- **直接输出 JSON 对象，第一个字符必须是 `{`**
- **不要用 ```json 或 ``` 代码块包裹**
- **不要在 JSON 前后加任何说明文字或思考过程**"""


async def bootstrap(db_session) -> None:
    """Run startup bootstrap. All steps are idempotent."""
    from .db import async_session_factory

    async with async_session_factory() as session:
        await _ensure_session_secret()
        await _ensure_admin_user(session)
        await _ensure_prompt_template(session)
        await _ensure_llm_provider(session)
        await _ensure_webhook_channel(session)
        await _ensure_analysis_module(session)
        await _sync_sources(session)


async def _sync_sources(session) -> None:
    """Project the YAML source specs onto source_configs. YAML is the truth."""
    from ..config import settings
    from ..ingest.registry import sync_from_dir

    result = await sync_from_dir(session, settings.sources_dir)
    if result["errors"]:
        # Loud, but not fatal: one broken spec must not stop the other sources
        # (or the whole app) from coming up.
        logger.error("bootstrap.source_specs_invalid", errors=result["errors"])


async def _ensure_session_secret() -> None:
    """Generate session secret, admin token and ingest token if not already set."""
    _persist_or_generate("MEGATRON_SESSION_SECRET", ".session_secret")
    _persist_or_generate("MEGATRON_ADMIN_TOKEN", ".admin_token")
    _persist_or_generate("MEGATRON_INGEST_TOKEN", ".ingest_token")
    _persist_or_generate("MEGATRON_DAY_TOKEN", ".day_token")


def _persist_or_generate(env_var: str, filename: str) -> None:
    """Load from file or env, or generate and persist."""
    if os.getenv(env_var):
        return

    secret_file = f"/app/data/{filename}"
    try:
        if os.path.exists(secret_file):
            with open(secret_file) as f:
                val = f.read().strip()
            if val:
                os.environ[env_var] = val
                return
    except OSError:
        pass

    val = secrets.token_urlsafe(48)
    os.environ[env_var] = val
    try:
        os.makedirs(os.path.dirname(secret_file), exist_ok=True)
        with open(secret_file, "w") as f:
            f.write(val)
    except OSError:
        logger.warning("bootstrap.cannot_persist", path=secret_file)


async def _ensure_admin_user(session) -> None:
    """Create default admin user if none exists."""
    from ..config import settings
    from .engine_models import User
    from .security import generate_token, hash_password

    result = await session.execute(select(User).limit(1))
    if result.scalar_one_or_none():
        return

    password = settings.admin_password
    generated = False
    if not password:
        # No configured password: mint a strong random one instead of the old
        # hardcoded "admin", and log it once so the operator can sign in.
        password = generate_token(18)
        generated = True

    user = User(
        username="admin",
        display_name="Administrator",
        password_hash=hash_password(password),
        is_active=True,
    )
    session.add(user)
    await session.commit()
    if generated:
        logger.warning(
            "bootstrap.admin_user_created_with_generated_password",
            username="admin",
            password=password,
            hint="Set MEGATRON_ADMIN_PASSWORD to control this; change it after first login.",
        )
    else:
        logger.info("bootstrap.admin_user_created", username="admin")


async def _ensure_prompt_template(session) -> None:
    """Create the built-in prompt templates if they are missing. Idempotent."""
    from ..engine.builtin import (
        DAILY_INTEL_V1,
        DAILY_INTEL_V1_DISPLAY,
        DAILY_INTEL_V1_NAME,
        DAILY_INTEL_V1_SCHEMA,
    )
    from .engine_models import PromptTemplate

    wanted = (
        (DEFAULT_PROMPT_NAME, DEFAULT_PROMPT_DISPLAY, DEFAULT_PROMPT_TEMPLATE, {}),
        (DAILY_INTEL_V1_NAME, DAILY_INTEL_V1_DISPLAY, DAILY_INTEL_V1, DAILY_INTEL_V1_SCHEMA),
    )

    created = []
    for name, display, template, schema in wanted:
        exists = (
            await session.execute(select(PromptTemplate).where(PromptTemplate.name == name))
        ).scalar_one_or_none()
        if exists:
            continue
        session.add(
            PromptTemplate(
                name=name,
                display_name=display,
                version=1,
                template=template,
                output_schema=schema,
                is_active=True,
            )
        )
        created.append(name)

    if created:
        await session.commit()
        logger.info("bootstrap.prompt_templates_created", names=created)


async def _ensure_llm_provider(session) -> None:
    """Create DeepSeek provider if API key is provided and no provider exists."""
    from .engine_models import LLMProvider
    from .security import encrypt_secret

    api_key = os.getenv("MEGATRON_DEEPSEEK_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return

    result = await session.execute(select(LLMProvider).where(LLMProvider.name == "deepseek"))
    if result.scalar_one_or_none():
        # Already exists, maybe update key
        return

    provider = LLMProvider(
        name="deepseek",
        model="deepseek/deepseek-chat",
        api_base="https://api.deepseek.com/v1",
        api_key=encrypt_secret(api_key),
        temperature=0.3,
        max_tokens=32768,
        enabled=True,
    )
    session.add(provider)
    await session.commit()
    logger.info("bootstrap.llm_provider_created")


async def _ensure_webhook_channel(session) -> None:
    """Create DingTalk channel if URL is provided."""
    from .engine_models import WebhookChannel
    from .security import encrypt_config

    webhook_url = os.getenv("MEGATRON_DINGTALK_URL")
    if not webhook_url:
        return

    result = await session.execute(select(WebhookChannel).where(WebhookChannel.kind == "dingtalk"))
    if result.scalar_one_or_none():
        return

    config = {"webhook_url": webhook_url}
    secret = os.getenv("MEGATRON_DINGTALK_SECRET")
    if secret:
        config["secret"] = secret

    channel = WebhookChannel(
        name="钉钉安全简报",
        kind="dingtalk",
        config=encrypt_config(config),
        enabled=True,
    )
    session.add(channel)
    await session.commit()
    logger.info("bootstrap.webhook_channel_created")


async def _ensure_analysis_module(session) -> None:
    """Create default analysis module if none exists."""
    from .engine_models import AnalysisModule, LLMProvider, PromptTemplate, WebhookChannel

    result = await session.execute(select(AnalysisModule).limit(1))
    if result.scalar_one_or_none():
        return

    # Need prompt and provider to exist first
    prompt_result = await session.execute(
        select(PromptTemplate).where(PromptTemplate.name == DEFAULT_PROMPT_NAME)
    )
    prompt = prompt_result.scalar_one_or_none()
    if not prompt:
        logger.warning("bootstrap.module_skipped", reason="no prompt template")
        return

    provider_result = await session.execute(select(LLMProvider).where(LLMProvider.name == "deepseek"))
    provider = provider_result.scalar_one_or_none()
    if not provider:
        logger.warning("bootstrap.module_skipped", reason="no llm provider")
        return

    channel_result = await session.execute(select(WebhookChannel).where(WebhookChannel.kind == "dingtalk"))
    channel = channel_result.scalar_one_or_none()

    module = AnalysisModule(
        name="twitter_security_briefing",
        description="每日推特安全信息流分析",
        source="soundwave",
        source_ref="",
        filter_config={"time_mode": "today", "filters": [], "max_items": 0},
        prompt_template_id=prompt.id,
        provider_id=provider.id,
        agent_backend="none",
        tools_config=[],
        webhook_channel_ids=[channel.id] if channel else [],
        schedule_cron="0 9 * * *",  # daily 09:00 UTC = 17:00 Beijing
        enabled=True,
    )
    session.add(module)
    await session.commit()
    logger.info("bootstrap.module_created")
