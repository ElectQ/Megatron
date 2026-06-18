# Megatron 设计文档与建设计划

> **定位**:Prompt 驱动的 LLM 分析中枢。
> 将多源数据通过可配置的分析模块（提示词 + 工具 + 模型）转化为结构化、可行动的情报，经 Webhook 推送。
> 首个用例：每日 Twitter 安全情报简报（数据来源：Soundwave）。

---

## 目录

1. [定位与设计哲学](#1-定位与设计哲学)
2. [已确认决策清单](#2-已确认决策清单)
3. [技术栈](#3-技术栈)
4. [架构总览](#4-架构总览)
5. [六大可插拔模块（核心）](#5-六大可插拔模块核心)
6. [AnalysisModule：配置组合的灵魂](#6-analysismodule配置组合的灵魂)
7. [一次运行的完整流转](#7-一次运行的完整流转)
8. [数据接入设计（Soundwave 衔接）](#8-数据接入设计soundwave-衔接)
9. [Agent 与工具调用机制](#9-agent-与工具调用机制)
10. [Key 管理与安全](#10-key-管理与安全)
11. [提示词管理](#11-提示词管理)
12. [Web UI 设计](#12-web-ui-设计)
13. [项目结构](#13-项目结构)
14. [分阶段建设计划（P0-P6）](#14-分阶段建设计划p0-p6)
15. [部署与运维](#15-部署与运维)

---

## 1. 定位与设计哲学

Megatron 不是"安全简报生成器"，而是一个**通用的、以 LLM 调用为核心的模块化分析系统**。Twitter 安全数据只是它的第一个数据源/用例。

**命名隐喻**：Soundwave（霸天虎情报官，负责侦察收集）→ Megatron（领袖，负责决策指挥）。延续性：**收集 → 分析 → 行动**。

**核心抽象**：不是"分析任务"的硬编码，而是 **「分析模块」(AnalysisModule)** = 六维可配置组合（Source + Filter + Prompt + Tool + Provider + WebhookChannel）。换组合即换任务，核心引擎零改动。

**设计原则**（Open/Closed）：
- 对扩展开放：新能力 = 加零件（代码插件）或换配置（UI 驱动）
- 对修改关闭：核心引擎只依赖抽象契约，永不因新增插件而改动
- 所有可变配置（提示词 / key / 筛选 / 映射）都在 DB，由 UI 驱动，而非硬编码

---

## 2. 已确认决策清单

| 维度 | 决策 |
|---|---|
| **定位** | Prompt 驱动的 LLM 分析中枢（通用，Twitter 安全是首个用例） |
| **语言/栈** | Python 3.11+ / uv |
| **Web 框架** | FastAPI + Uvicorn |
| **存储** | SQLAlchemy 2.0 async + SQLite + Alembic |
| **调度** | APScheduler（AsyncIOExecutor） |
| **LLM** | LiteLLM（多 provider 可配置） |
| **Agent** | 自研薄循环（~150 行）+ 抽象接口可替换 |
| **工具层** | httpx + trafilatura + Playwright（兜底 JS 页） |
| **前端** | HTMX + Jinja2 + Tailwind/DaisyUI |
| **Webhook 出站** | Telegram / 飞书 / 企业微信 / 钉钉 |
| **部署** | 自有 VPS，Caddy 自动 HTTPS |
| **鉴权** | 轻量（admin token + session） |
| **密钥** | Fernet 加密入库，MASTER_KEY 在 .env |
| **数据接入** | 推（Soundwave curl POST）+ 拉（git clone 兜底） |
| **推送认证** | Bearer Token（32 字节随机串，可后期加 HMAC） |
| **数据时效** | 当天内，拉兜底每 6h |
| **输出语言** | 中文为主（CVE/技术名词保留英文原文） |
| **Soundwave 仓库** | Public（拉兜底无需 PAT） |

---

## 3. 技术栈

### 语言
**Python 3.11+**（唯一语言）。理由：与 Soundwave 一致、asyncio 适合 agent 网络循环、LLM/抓取生态最全。前端用服务端渲染（HTMX）不引入第二门语言。

### 组件清单

| 层 | 组件 | 选型 | 理由 |
|---|---|---|---|
| Web 框架 | HTTP 服务 | FastAPI + Uvicorn | async、Pydantic 校验、自动 OpenAPI |
| 数据层 | ORM / 存储 | SQLAlchemy 2.0 async + SQLite + Alembic | 单文件零运维、async、历史查询 |
| 配置 | settings | pydantic-settings + `.env` | 类型安全 |
| 任务调度 | 定时器 | APScheduler (AsyncIOExecutor) | 进程内 cron，无需外部 broker |
| 后台任务 | 长任务 | FastAPI BackgroundTasks + 自建 TaskRunner（DB 持久化状态机） | agent 任务几分钟级，支持重启恢复 |
| LLM 抽象 | 多 provider | LiteLLM | 一套接口调 100+ 模型，含 function calling |
| Agent 循环 | tool-use | 自研轻量 loop（~150 行） | 贴合模块化抽象，可控可调试 |
| 工具层 | URL 抓取 | httpx + trafilatura + Playwright | 静态页用 trafilatura，JS 重页兜底 |
| 模板 | 提示词引擎 | Jinja2 | 变量注入、条件、循环，UI 可编辑 |
| 前端 | 管理 UI | HTMX + Jinja2 + DaisyUI | 服务端渲染、零 JS 构建 |
| Webhook 出站 | 多平台 | httpx + 自建渲染器 | 每平台消息格式不同，渲染器隔离 |
| 安全 | 鉴权 | itsdangerous session + admin token | 轻量，单用户够用 |
| 密钥 | 加密存储 | cryptography (Fernet) | provider/webhook token 落库前加密 |
| 反代/HTTPS | 部署 | Caddy | 自动 HTTPS，单文件配置 |
| 可观测 | 日志/指标 | structlog + 内置 dashboard | 记录每次运行 token/成本/工具调用 |
| 质量 | 测试 | pytest + pytest-asyncio + httpx ASGI | 与 Soundwave 一致 |

---

## 4. 架构总览

```
┌─────────────────────────────────────────────────────────────────────┐
│  入口层 (FastAPI)                                                     │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌────────────┐ │
│  │ Web UI(HTMX) │ │ REST API     │ │ Ingest Webhook│ │ 健康检查   │ │
│  └──────┬───────┘ └──────┬───────┘ └──────┬────────┘ └────────────┘ │
└─────────┼────────────────┼────────────────┼─────────────────────────┘
          │                │                │
┌─────────▼────────────────▼────────────────▼─────────────────────────┐
│  编排层 (Orchestration)        ← 系统的"指挥官"                        │
│  ┌────────────┐  ┌──────────────────┐  ┌──────────────────────────┐ │
│  │ Scheduler  │→ │ Module Runner    │→ │ TaskRunner(状态机,持久)   │ │
│  │ (cron/UI)  │  │ (加载模块配置)    │  │ running/failed/cancelled │ │
│  └────────────┘  └────────┬─────────┘  └──────────────────────────┘ │
└────────────────────────────┼────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────┐
│  执行层 (Engine)            ← 系统的"心脏"：六维可插拔                 │
│  ┌─────────┐ ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌─────────────┐ │
│  │ Sources │ │ Filters │ │ Prompts  │ │ Tools    │ │ LLMProvider │ │
│  │ (数据源) │ │ (价值筛)│ │ (模板)   │ │ (agent)  │ │ (模型)      │ │
│  └─────────┘ └─────────┘ └──────────┘ └──────────┘ └─────────────┘ │
│                              │                                       │
│                  Agent Loop:  LLM ⇄ Tools(多轮) → Result             │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────────┐
│  输出层 (Delivery)                                                    │
│  ┌─────────────────────────────────────────────┐ ┌────────────────┐ │
│  │ Webhook Channels (渲染器)                     │ │ Result Store   │ │
│  │ Telegram / 飞书 / 企微 / 钉钉 / Discord       │ │ (SQLite)       │ │
│  └─────────────────────────────────────────────┘ └────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────────┐
│  基础设施层                                                            │
│  SQLite + Alembic │ Secrets(cryptography) │ structlog │ APScheduler  │
└───────────────────────────────────────────────────────────────────────┘
```

---

## 5. 六大可插拔模块（核心）

六个模块都是独立可替换的单元。扩展机制分两类：

| 类型 | 模块 | 加组件方式 | 要重启吗 |
|---|---|---|---|
| **代码插件** | Source / Tool / WebhookChannel | 加一个文件 + `@register` 装饰器 | 重启（P6 加热加载） |
| **DB 配置** | Prompt / LLMProvider / Filter | UI 直接编辑 | 不重启 |

### 5.1 Source（数据源）—— "从哪拿数据"

**契约**：
```python
class BaseSource(ABC):
    name: str
    async def fetch(self, since: datetime) -> list[Item]: ...
```

`Item` 是标准化结构，所有源输出统一形态：
```python
@dataclass
class Item:
    id: str                # 唯一ID（幂等用）
    source: str            # 来源标记
    title: str             # 标题（可空）
    content: str           # 正文
    url: str               # 原文链接
    author: str
    published_at: datetime
    raw: dict              # 原始数据（保留兜底）
    metadata: dict         # 互动量/标签等
```

**当前实现**：`TwitterListSource`（解析 Soundwave JSON）
**未来扩展**：RSSSource / GHSASource / HackerNewsSource / WebhookSource / FileSource / MailArchiveSource

> 对引擎而言，所有源都是 `list[Item]`，换源不改分析逻辑。

### 5.2 Filter（价值筛）—— "留哪些丢哪些"

**契约**：
```python
class BaseFilter(ABC):
    def score(self, item: Item) -> float:           # 0.0~1.0 重要度
    def should_include(self, item: Item) -> bool:   # 留还是丢
```

**当前实现**：InteractionFilter（互动量阈值）+ DedupFilter（转推链去重）+ KeywordFilter（关键词白/黑名单）
**未来扩展**：LLMPreFilter（便宜小模型预筛）/ TrendFilter（爆量话题加权）

### 5.3 Prompt（提示词）—— "AI 怎么分析"

**契约**（非代码，DB 记录 + Jinja2 模板）：
```python
class PromptTemplate:
    id: str
    template: str          # Jinja2，UI 可编辑
    output_schema: dict    # 输出 JSON 的 schema
    version: int           # 版本控制
```

**当前实现**：`daily_security_briefing`（内置起步模板）
**未来扩展**：weekly_trend_summary / cve_deep_dive / new_tool_radar（纯 UI 操作）

### 5.4 Tool（工具）—— "AI 能调什么"

**契约**：
```python
class BaseTool(ABC):
    name: str
    schema: dict           # JSON Schema，给 LLM 看的调用说明
    async def run(self, **args) -> dict: ...
```

**当前实现**：FetchUrlTool / ExtractTextTool / LookupCveTool
**未来扩展**：SearchTool / TranslateTool / PlaywrightTool / WhoisLookupTool

### 5.5 LLMProvider（模型）—— "用哪个 AI"

**契约**：
```python
class LLMProvider:
    async def chat(self, messages, tools=None) -> Response:
        return await litellm.acompletion(
            model=self.config.model,    # 换厂商只改这字符串
            messages=messages, tools=tools,
        )
```

**当前实现**：LiteLLM 统一封装（DeepSeek / OpenAI / Claude / 通义 / Ollama 都是改 model 字符串）
**换厂商示例**：`deepseek/deepseek-chat` / `openai/gpt-4o` / `anthropic/claude-3-5-sonnet`

### 5.6 WebhookChannel（推送）—— "结果发到哪"

**契约**：
```python
class BaseChannel(ABC):
    async def send(self, result: AnalysisResult) -> None:
        payload = self.render(result)    # 转平台格式
        await self._post(payload)
```

**当前实现**：TelegramChannel / FeishuChannel / WecomChannel / DingTalkChannel
**未来扩展**：SlackChannel / DiscordChannel / EmailChannel / CustomChannel

---

## 6. AnalysisModule：配置组合的灵魂

六个模块是"零件"，真正的分析任务是**把零件组装起来**的配置记录，存在 DB：

```
AnalysisModule "每日安全简报"
├── source:        TwitterListSource(sec_list)         ← 模块1
├── filter:        InteractionFilter(阈值10) + DedupFilter  ← 模块2
├── prompt:        daily_security_briefing (v3)        ← 模块3
├── tools:         [FetchUrl, LookupCve]               ← 模块4
├── llm_provider:  deepseek-chat                       ← 模块5
├── webhooks:      [Telegram_我, 飞书_团队]             ← 模块6
└── schedule:      cron "30 6 * * *"
```

**换任务 = 换组合，引擎代码一字不改**：
- 做周报 → 复制 Module，改 prompt + schedule
- 加飞书推送 → 同一 Module 加挂一个 webhook channel
- 换 Claude → 改 llm_provider 字段

---

## 7. 一次运行的完整流转

```
[APScheduler cron] 或 [UI 点"运行"]
        │
        ▼
ModuleRunner.load(module_id)
   → 从 DB 读取六维配置
   → 用 Registry 实例化 Source/Filter/Tools/Provider/Webhooks
        │
        ▼
Source.fetch(since=24h)         ← 插件: TwitterListSource 读 Soundwave
        │ list[Item]
        ▼
Filter.score → 取 Top 30        ← 插件: 可换规则/小模型
        │
        ▼
Prompt.render(items, ctx)       ← Jinja2 模板 (DB, UI 可改)
        │
        ▼
AgentLoop:                      ← 自研循环，调 LiteLLM function calling
   for turn in 1..MAX:
     LLM.chat(prompt, tools) ──┐
        │ tool_call?           │
        ├─是→ Tool.run(arg)────┘  ← 插件: fetch_url 抓 CVE 页
        │       ↑ 结果回灌 LLM
        └─否→ break → final Result
        │
        ▼
Schema.validate(Result, schema) ← pydantic 校验输出契约
        │
        ▼
TaskRunner.persist(run, tokens, tool_calls_log)  ← DB 持久化
        │
        ▼
WebhookChannel.send(Result)     ← 插件: 渲染→Telegram/飞书/钉钉
        │
        ▼
UI 刷新 / Telegram 收到推送
```

---

## 8. 数据接入设计（Soundwave 衔接）

**推拉结合**：主动方能推（Soundwave），被动方能拉（Megatron）。

```
┌─── GitHub Actions (每日 05:00 UTC) ───┐
│  soundwave crawl → commit → push        │
│             │                            │
│             ▼  (+1 step: curl POST)      │
└─────────────┼────────────────────────────┘
              │ HTTPS + Bearer token
              ▼
┌─── Megatron VPS ─────────────────────────┐
│  POST /api/ingest/twitter                 │  ← 主路径(秒级)
│   → 验签 → 幂等去重 → SQLite              │
│                                            │
│  APScheduler 每 6h:                       │  ← 兜底(小时级)
│   git clone soundwave → 扫 data/ → ingest │
│   (补推失败/历史回灌)                      │
└────────────────────────────────────────────┘
```

### 8.1 推模式（A）—— Soundwave POST

Soundwave 的 `crawl.yml` 加一步（概念草图）：
```yaml
- name: Push to Megatron
  if: success()
  env:
    MEGATRON_URL: ${{ secrets.MEGATRON_URL }}
    MEGATRON_TOKEN: ${{ secrets.MEGATRON_TOKEN }}
  run: |
    for f in data/$(date -u +%Y-%m-%d)/*.json; do
      curl -fsS -X POST "$MEGATRON_URL/api/ingest/twitter" \
        -H "Authorization: Bearer $MEGATRON_TOKEN" \
        -H "Content-Type: application/json" \
        --data-binary @"$f" \
        --retry 3 --retry-delay 10 \
        || echo "::warning::Push failed (data still in git)"
    done
```

**Megatron ingest 端点契约**：
```
POST /api/ingest/twitter
Authorization: Bearer <token>
Body: Soundwave 的 {date, list_id, list_name, crawled_at, count, tweets:[...]}

处理:
1. 验 token (constant-time 比较)
2. 解析 → 按 (tweet_id) 幂等 upsert
3. 返回 {ingested: N, duplicated: M}
```

### 8.2 拉兜底（B）—— git clone

```
APScheduler 每 6 小时:
1. git clone --depth 1 https://github.com/<you>/Soundwave (浅克隆，public 无需 PAT)
2. 遍历 data/*/*.json
3. 对比 SQLite 已有的 (date, list_id) 集合，只 ingest 新的
4. 清理临时 clone
```

- 顺带解决"历史数据回灌"和"首次部署验证"
- 幂等保证：和推送撞车也只更新不新增

### 8.3 幂等

`(tweet_id)` 唯一约束，重复推送/拉取无副作用。推和拉可并行运行。

### 8.4 认证

起步 Bearer Token（32 字节随机串，走 HTTPS）。威胁模型：防公网投毒，足够。后期可加 HMAC 签名（防篡改/重放）。

| 认证强度 | 防 | 实现量 | 适合 |
|---|---|---|---|
| Bearer Token | 陌生人投毒 | ~10 行 | HTTPS + 个人项目 ✅ |
| HMAC 签名 | + 篡改/重放 | ~30 行 | 公网关键接口 |
| GitHub OIDC | + 消灭静态密钥 | ~150 行 | 企业多租户（过度设计） |

---

## 9. Agent 与工具调用机制

### 9.1 抽象接口（可替换）

```python
class AgentBackend(Protocol):
    async def run(self, prompt: str, tools: list[Tool]) -> AgentResult: ...
```

### 9.2 默认实现：自研薄循环（~150 行）

```python
class LiteAgentLoop(AgentBackend):
    async def run(self, prompt, tools):
        messages = [{"role": "user", "content": prompt}]
        for _ in range(self.max_turns):
            resp = await self.llm.chat(messages, tools=tools.schemas())
            if not resp.tool_calls:
                return AgentResult(content=resp.content, trace=self.trace)
            messages = await self._exec_tools(resp.tool_calls, tools, messages)
        return AgentResult(...)
```

### 9.3 切换实现

Module 配置 `agent_backend: "lite" | "langgraph"`，引擎零改动。未来想用框架时加 `LangGraphBackend(AgentBackend)` 子类即可。

### 9.4 工具调用流程

```
LLM 返回 tool_call({name:"fetch_url", args:{url:"..."}})
  → ToolRegistry 查找 fetch_url
  → FetchUrlTool.run(url) → {text, url}
  → 结果回灌 messages（role: tool）
  → LLM 下一轮决定继续调工具 or 输出最终结果
```

**安全保障**：最大轮次（如 10）、单工具超时、工具白名单（每个 Module UI 勾选）。

---

## 10. Key 管理与安全

### 10.1 存储设计

```python
class SecretVault:
    """密钥用 Fernet 对称加密后入库，主密钥来自 .env 的 MASTER_KEY"""
    def store(self, plaintext: str) -> str:    # → 密文入库
    def load(self, ciphertext: str) -> str:    # → 用时解密
```

### 10.2 安全红线

- 明文 key **永不入库**、**永不进日志**、**永不返回前端**（API 只返回 `sk-***后4位`）
- 主密钥（MASTER_KEY）只存在 `.env`/环境变量，不进 DB 不进 git
- `.env` 在 `.gitignore`，`.env.example` 只放占位符
- UI 的"测试"按钮真发一次请求验证 key 有效

### 10.3 Web UI 密钥管理页

- LLM Providers：DeepSeek / OpenAI / ... [测试] [编辑] [删除]
- Webhook Tokens：Telegram Bot / 飞书应用 / ... [测试] [编辑]

---

## 11. 提示词管理

### 11.1 能力

| 能力 | 实现 | 价值 |
|---|---|---|
| UI 实时编辑 | 文本框 + Jinja2，保存即生效 | 想改就改 |
| 变量预览 | 保存前用真实数据渲染一次 | 改完能验证 |
| 版本控制 | 每次保存生成新版本 | 改坏能回滚 |
| A/B 对比 | 两版本同时跑一次对比 | 调优有依据 |
| 模板复用 | 一个模板可被多 Module 引用 | 不重复造轮子 |
| Schema 绑定 | 每模板绑定输出 JSON schema | 输出稳定可解析 |

### 11.2 起步模板

`daily_security_briefing`：
```
你是安全情报分析师。以下是今日 {{ item_count }} 条推文：
{% for t in top_items %}
- {{ t.author }}: {{ t.content[:100] }}
{% endfor %}

请输出 JSON: {briefing, items:[{title, cve, severity, summary}]}
```

---

## 12. Web UI 设计

### 12.1 技术决策

HTMX + Jinja2 + DaisyUI（服务端渲染，零 JS 构建）。后端纯 REST API，HTMX 只是默认消费者 —— 未来加 SPA 时 `/api/*` 一行不改。

### 12.2 页面规划

| 页面 | 功能 |
|---|---|
| Dashboard | 最近运行、今日数据量、推送状态 |
| 数据浏览 | items 列表、筛选、详情 |
| 分析模块 | Module 增删改查、六维配置组合 |
| 提示词编辑器 | Jinja2 编辑、变量预览、版本管理 |
| 运行历史 | runs 列表、详情（输入/输出/工具调用日志/token成本） |
| 手动运行 | 选 Module 触发、实时进度 |
| LLM Providers | 增删改查、测试连接 |
| Webhook Channels | 增删改查、测试发送 |
| 调度管理 | cron 配置、启停 |
| 密钥管理 | 加密 key 的全生命周期 |
| 插件管理 | 查看/重载已注册的代码插件 |

### 12.3 鉴权

轻量：admin token + itsdangerous session。个人项目防公网随意访问即可。

---

## 13. 项目结构

```
megatron/
├── pyproject.toml              uv 项目配置
├── .env.example                环境变量占位
├── .gitignore
├── docs/PLAN.md                本文档
├── src/megatron/
│   ├── main.py                 FastAPI app 入口
│   ├── config.py               pydantic-settings
│   ├── core/                   基础设施
│   │   ├── registry.py         全局插件注册表（六类）
│   │   ├── db.py               SQLAlchemy async engine
│   │   ├── models.py           ORM 模型
│   │   ├── migrations/         Alembic
│   │   ├── security.py         鉴权 + 密钥加密（Fernet）
│   │   └── logging.py          structlog 配置
│   ├── engine/                 系统心脏（不依赖任何具体插件）
│   │   ├── runner.py           ModuleRunner：加载配置组合
│   │   ├── task_runner.py      状态机：持久化/重试/取消
│   │   ├── agent.py            AgentBackend 抽象
│   │   ├── agent_loop.py       LiteAgentLoop 默认实现
│   │   └── template.py         Jinja2 渲染
│   ├── plugins/                所有可插拔实现（每类一目录）
│   │   ├── sources/
│   │   │   ├── base.py         BaseSource 抽象
│   │   │   └── twitter.py      TwitterListSource
│   │   ├── filters/
│   │   │   ├── base.py
│   │   │   ├── interaction.py
│   │   │   ├── dedup.py
│   │   │   └── keywords.py
│   │   ├── tools/
│   │   │   ├── base.py
│   │   │   ├── fetch_url.py
│   │   │   ├── extract_text.py
│   │   │   └── lookup_cve.py
│   │   └── webhooks/
│   │       ├── base.py
│   │       ├── telegram.py
│   │       ├── feishu.py
│   │       ├── wecom.py
│   │       └── dingtalk.py
│   ├── llm/
│   │   └── provider.py         LiteLLM 封装 + Registry
│   ├── ingest/                 入站数据接入
│   │   ├── api.py              /api/ingest/* 端点
│   │   └── puller.py           git clone 兜底
│   ├── scheduler.py            APScheduler 配置
│   └── web/                    Web UI
│       ├── routes.py           /ui/* 路由
│       ├── templates/          Jinja2 模板
│       └── static/             HTMX/DaisyUI
├── tests/
│   ├── test_engine/
│   ├── test_plugins/
│   └── test_ingest/
└── Caddyfile                   Caddy 反代配置示例
```

> **核心纪律**：`engine/` 只依赖各 `plugins/*/base.py` 的抽象基类，**不知道任何具体实现**。新增插件 = 加文件 + `@register`，引擎自动发现。

---

## 14. 分阶段建设计划（P0-P6）

### P0 基座
**目标**：能跑的空壳 + dashboard
- [ ] uv 工程、目录结构
- [ ] FastAPI 骨架（健康检查、错误处理）
- [ ] pydantic-settings 配置 + `.env.example`
- [ ] SQLAlchemy async + SQLite + Alembic 初始迁移
- [ ] core ORM models（items / modules / runs / providers / channels）
- [ ] 轻量鉴权（admin token + session）
- [ ] structlog 日志
- [ ] 基础 Web UI 布局（DaisyUI）

**验证**：`uv run uvicorn` 启动，访问 dashboard，DB 表已建。

### P1 数据接入
**目标**：有数据可分析
- [ ] Item 标准化模型
- [ ] BaseSource 抽象 + Registry
- [ ] TwitterListSource（解析 Soundwave JSON）
- [ ] `/api/ingest/twitter` 端点（Bearer token + 幂等 upsert）
- [ ] git clone 拉兜底（APScheduler 每 6h）
- [ ] 历史数据回灌脚本
- [ ] 数据浏览 UI（列表 + 筛选 + 详情）

**验证**：Soundwave 数据推送/拉取后，UI 能浏览到 items。

### P2 分析引擎（基础）
**目标**：打通基础 LLM 循环（先不接工具）
- [ ] LiteLLM provider 抽象 + 加密存储
- [ ] Provider 配置 UI + 测试连接
- [ ] BaseFilter + InteractionFilter/DedupFilter
- [ ] Filter 配置 UI（阈值）
- [ ] Jinja2 提示词引擎
- [ ] 提示词编辑器 UI（编辑/版本/预览）
- [ ] AnalysisModule CRUD（六维配置）
- [ ] ModuleRunner（单次运行，无 agent）
- [ ] 输出 schema 校验（pydantic）
- [ ] 运行历史 UI（输入/输出/token）

**验证**：创建 Module，手动运行，看到 LLM 输出并存历史。

### P3 Agent + 工具
**目标**：具备 agentic 能力
- [ ] BaseTool 抽象 + Registry
- [ ] FetchUrlTool / ExtractTextTool / LookupCveTool
- [ ] AgentBackend 抽象 + LiteAgentLoop（~150 行）
- [ ] Module 配置 `agent_backend` 字段
- [ ] 工具白名单（UI 勾选）
- [ ] max_turns / 超时 / 错误处理
- [ ] 工具调用日志（记 DB，UI 展示迭代过程）
- [ ] httpx + trafilatura（静态页）
- [ ] Playwright 兜底（JS 重页，可选）

**验证**：LLM 在分析中主动调 fetch_url 抓详情，日志可见每轮迭代。

### P4 Webhook 推送
**目标**：能送达
- [ ] BaseChannel 抽象 + Registry
- [ ] TelegramChannel / FeishuChannel / WecomChannel / DingTalkChannel
- [ ] 各平台渲染器（Result → 平台 payload）
- [ ] Channel 配置 UI（加密 token + 测试发送）
- [ ] Module → Channel 映射
- [ ] 运行后自动推送
- [ ] 推送失败重试 + 日志

**验证**：Module 运行完成，Telegram/飞书/钉钉收到推送。

### P5 调度 + 首用例
**目标**：第一个完整用例上线
- [ ] APScheduler cron 配置 UI
- [ ] 调度启停
- [ ] 编写 `daily_security_briefing` 内置 Module（提示词 + schema + 工具）
- [ ] 端到端联调：Soundwave 推送 → 定时分析 → Telegram 推送
- [ ] Soundwave crawl.yml 加推送 step

**验证**：每天自动收到安全简报。

### P6 打磨
**目标**：生产可用
- [ ] 成本/token 统计 dashboard
- [ ] 错误重试策略
- [ ] 运行取消
- [ ] 审计日志
- [ ] 廉价预筛（规则/小模型先滤噪音再喂主分析）
- [ ] 插件热加载（`/api/admin/plugins/reload`）
- [ ] 文档（README + 部署指南）
- [ ] 测试覆盖

**验证**：稳定运行一周无事故。

---

## 15. 部署与运维

### 15.1 部署（VPS + Caddy）

```
VPS
├── megatron/          uv run uvicorn（systemd 或 supervisor 守护）
├── Caddy              反代 + 自动 HTTPS（单域名）
└── SQLite             单文件，随项目
```

**Caddyfile 示例**（P0 产出）：
```
megatron.yourdomain.com {
    reverse_proxy localhost:8000
}
```

### 15.2 启动命令

```bash
# 安装
uv sync

# 数据库迁移
uv run alembic upgrade head

# 启动
uv run uvicorn megatron.main:app --host 0.0.0.0 --port 8000
```

### 15.3 环境变量（`.env.example`）

```bash
# 核心配置
MEGATRON_ENV=production
MEGATRON_SECRET_KEY=change-me-to-random-string
MEGATRON_MASTER_KEY=change-me-fernet-key           # 密钥加密主密钥
MEGATRON_ADMIN_TOKEN=change-me-32-byte-random      # Web UI admin token

# 数据库
DATABASE_URL=sqlite+aiosqlite:///./megatron.db

# 数据接入
INGEST_TOKEN=change-me-32-byte-random              # Soundwave 推送认证
SOUNDWAVE_REPO_URL=https://github.com/<you>/Soundwave

# 服务
BASE_URL=https://megatron.yourdomain.com
```

### 15.4 可观测

- structlog 结构化日志
- 内置 dashboard：每次运行的 token/成本/工具调用/耗时
- APScheduler 运行日志
- ingest 端点访问日志

---

## 附录：可演进性总账

| 扩展诉求 | 架构如何保证 |
|---|---|
| 换 LLM 厂商 | 改一行配置字符串；新厂商加 50 行子类 |
| agent 实现想换 | 抽象接口隔离，Module 配置切换，engine 零改 |
| 前端想换 SPA | 后端纯 REST API，HTMX 是默认视图非约束，随时加 SPA |
| Key 安全管理 | Fernet 加密入库，UI 全生命周期，永不泄露 |
| 提示词方便改 | DB + Jinja2 + 版本 + 预览 + 回滚，UI 即改即用 |
| 加新数据源 | Source 子类 + 注册，engine 自动发现 |
| 加新工具 | Tool 子类 + 注册，UI 勾选启用 |
| 加新推送平台 | Channel 子类 + 注册，UI 配置 |

**一句话**：六个模块 = 六类零件；AnalysisModule = 装配图；引擎 = 通用装配机。加新能力 = 加零件或换装配图，永远不动装配机本身。
