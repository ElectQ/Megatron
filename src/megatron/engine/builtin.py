"""Builtin default prompt templates and seed data."""

from __future__ import annotations

DAILY_SECURITY_BRIEFING = """你是一名信息安全情报分析师。下面是采集到的 {{ item_count }} 条推文（来自安全研究者/从业者）：

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

• 标题 — 一句话解读 [原文]
• 标题 — 一句话解读 [文章]
• ...

⚪ 速览 Z 条

标题 [链接] | 标题 [链接] | 标题 | 标题 | ...
```

**格式规则**：
- 标题用日期格式：`⚡ 推特安全信息流简报 · 2026-06-17`
- 第二行是统计行：`共 N 条 · 必看 X · 推荐 Y · 速览 Z`
- **不要顶部概述/趋势段**，直接进必看
- 必看档：每条用 bullet（•）列 2-5 个要点（数量自适应，简单内容少写、复杂的多写），后面跟 `@author · [链接]`
- 必看档标题后加 `🆕`（new）或 `♻️`（reshare）标注
- 推荐档：一行一条，`• 标题 — 一句话解读 [链接]`
- 速览档：用管道符 `|` 分隔，仅标题+链接，紧凑排列（每行 3-5 个）
- 某档为空就不写该区块
- 控制总长度在 3000 字符内（Telegram/手机友好）

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
  "report_markdown": "⚡ 推特安全信息流简报 · 2026-06-17\\n共 N 条 · ...\\n\\n🔴 必看...",
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
- **不要在 JSON 前后加任何说明文字或思考过程**
"""

DEFAULT_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "report_markdown": {"type": "string", "description": "分档简报 Markdown"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": ["vuln", "tool", "research", "threat", "incident", "advisory"],
                    },
                    "tier": {
                        "type": "string",
                        "enum": ["must", "recommended", "quick"],
                        "description": "必看/推荐/速览",
                    },
                    "freshness": {
                        "type": "string",
                        "enum": ["new", "reshare"],
                        "description": "new=今日首发 / reshare=今日讨论的旧成果",
                    },
                    "cve": {"type": "string"},
                    "summary": {"type": "string"},
                    "links": {"type": "array", "items": {"type": "string"}},
                    "source_url": {"type": "string"},
                    "author": {"type": "string"},
                    "artifact": {
                        "type": "string",
                        "enum": ["repo", "article", "poc", "video", "none"],
                    },
                },
                "required": ["title", "category", "tier", "freshness", "summary", "source_url"],
            },
        },
    },
    "required": ["report_markdown", "items"],
}


DAILY_INTEL_V1_NAME = "daily_intel_v1"
DAILY_INTEL_V1_DISPLAY = "每日情报分级（门铃 / 日刊）"

DAILY_INTEL_V1 = """你是"个人安全情报雷达"的分级引擎。今天是 {{ now }}。

你的产出不是简报，而是**分级**：决定每条情报值不值得打断用户、值不值得读。
真正的稿件由系统渲染，你只负责判断和一句话说清。

## 关注意图（判断"关不关我事"的唯一标准）
{% if ctx.intent %}
- 首要：{{ ctx.intent.primary | join('、') }}
- 次要：{{ ctx.intent.secondary | join('、') }}
{% else %}
- 首要：本地/自托管 AI Agent 的安全问题
- 次要：高危可利用漏洞、可复现的攻击手法、值得上手的开源工具
{% endif %}

## 分级（tier，严格用这五个值）
- `must_see_push` —— **今天必须打断用户**。最多 {{ ctx.caps.must_see_push_max | default(3) }} 条，**宁缺毋滥，0 条也完全可以接受**。
  只有满足"和首要意图直接相关"且"今天不知道会有实际损失"才给。
- `must_see_page` —— 重要，但不值得打断；日刊里放最前面。
- `recommend`    —— 值得读。
- `skim`         —— 扫一眼就够。
- `drop`         —— 不该占用户时间。**大胆用它。**

## 必须 drop 的（黑名单）
- 八卦、骂战、人身攻击、纯情绪输出
- 招聘、广告、会议宣传、抽奖、涨粉
- 纯转发无增量观点、标题党无实质内容
- 与意图完全无关的泛科技新闻

## 每条必须回填的字段
- `external_id` 和 `source_id`：**原样照抄输入里的值，一个字符都不要改**。
  这两个是系统用来回查原文的键，编造或改写会导致这条被直接丢弃。
- `one_liner`：一句话说清**发生了什么**（≤40 字，不要复述标题，不要"某某发文称"）。
- `why_for_me`：一句话说清**为什么和这个用户有关**（≤35 字）。必须扣住上面的意图，
  不能写成泛泛的"值得关注"。写不出具体关系的，说明它不该是高档位。
- `actionability`：`none` / `read` / `watch` / `try`
- `scores`：`relevance`(0-3) `actionability`(0-3) `confidence`(0-1) `noise_risk`(0-1)

## 输入
共 {{ item_count }} 条：

{% for item in items %}
---
external_id: {{ item.external_id }}
source_id: {{ item.source_id }}
author: {{ item.author }}{% if item.author_name %} ({{ item.author_name }}){% endif %}
metrics: {{ item.metrics }}
content: {{ item.content }}
{% if item.links %}links: {{ item.links | join(' ') }}{% endif %}
{% endfor %}

## 输出
只输出一个 JSON 对象，第一个字符必须是 `{`。不要用 ``` 包裹，不要有任何解释文字。

{
  "items": [
    {"external_id": "...", "source_id": "...", "tier": "must_see_push",
     "one_liner": "...", "why_for_me": "...", "bullets": ["..."],
     "actionability": "try", "topics": ["ai_agent", "rce"],
     "scores": {"relevance": 3, "actionability": 3, "confidence": 0.8, "noise_risk": 0.1}}
  ],
  "push_item_ids": ["..."]
}

`push_item_ids` 填你认为最该打断用户的那几条的 external_id（按重要性排序）。
注意：系统会自己按 tier 重新计算真正推送哪几条并强制截断，你填的只作为排序参考——
所以**不要**为了让某条被推送而虚报它的 tier。
"""

DAILY_INTEL_V1_SCHEMA = {
    "type": "object",
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["external_id", "source_id", "tier", "one_liner"],
                "properties": {
                    "external_id": {"type": "string"},
                    "source_id": {"type": "string"},
                    "tier": {
                        "type": "string",
                        "enum": [
                            "must_see_push",
                            "must_see_page",
                            "recommend",
                            "skim",
                            "drop",
                        ],
                    },
                    "one_liner": {"type": "string"},
                    "why_for_me": {"type": "string"},
                    "bullets": {"type": "array", "items": {"type": "string"}},
                    "actionability": {
                        "type": "string",
                        "enum": ["none", "read", "watch", "try"],
                    },
                    "topics": {"type": "array", "items": {"type": "string"}},
                    "scores": {"type": "object"},
                },
            },
        },
        "push_item_ids": {"type": "array", "items": {"type": "string"}},
    },
}


_SEEDS = (
    ("daily_security_briefing", "推特安全信息流简报", DAILY_SECURITY_BRIEFING, DEFAULT_OUTPUT_SCHEMA),
    (DAILY_INTEL_V1_NAME, DAILY_INTEL_V1_DISPLAY, DAILY_INTEL_V1, DAILY_INTEL_V1_SCHEMA),
)


async def seed_defaults(session) -> dict:
    """Create the built-in prompt templates. Idempotent."""
    from sqlalchemy import select

    from ..core.engine_models import PromptTemplate

    seeded, skipped = [], []
    for name, display, template, schema in _SEEDS:
        existing = (
            await session.execute(select(PromptTemplate).where(PromptTemplate.name == name))
        ).scalars().all()
        if existing:
            skipped.append(name)
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
        seeded.append(name)

    await session.commit()
    return {"seeded": seeded, "skipped": skipped}


__all__ = [
    "DAILY_INTEL_V1",
    "DAILY_INTEL_V1_NAME",
    "DAILY_INTEL_V1_SCHEMA",
    "DAILY_SECURITY_BRIEFING",
    "DEFAULT_OUTPUT_SCHEMA",
    "seed_defaults",
]
