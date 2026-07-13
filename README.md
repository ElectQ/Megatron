# Megatron

个人安全/技术情报雷达的**中枢（Core）**：多源采集入库 → 规则 + LLM 分级 → 薄推送（门铃）+ 厚日刊。

```text
Collector（采集作业）              Megatron（Core）
─────────────────────            ────────────────────────────────
Soundwave / GH / RSS  ──push──▶  统一 Item 入库
                      ◀─pull───  （去重键：source_id + external_id）
                                        │
                                        ▼
                                  日任务：汇合 + LLM 分级
                                        │
                          ┌─────────────┴─────────────┐
                          ▼                           ▼
                   门铃（≤3 条，薄）             日刊页（全量，厚）
                   钉钉 / 飞书 / TG / 企微        /day/{date}?k=…
```

**推送不是简报。** 推送只回答"要不要现在停下手上的事"，最多 3 条；
剩下的全部在日刊页，一个链接之外。条数上限由服务端强制截断，不靠 prompt 自觉。

## 边界

| | 负责 | 不负责 |
|---|---|---|
| **Collector** | 爬取、规范化、投递日包 | 分级、推送、用户意图 |
| **Megatron** | 入库、汇合、分析、分级、推送 | 持有各站 cookie 去爬 |

Collector 是**定时采集作业，不是 agent**。

## 源接入三角

| 方式 | 什么时候用 | adapter |
|---|---|---|
| **HTTP Push（主）** | Collector 能主动 POST 进来 | `http_push` → `POST /api/ingest/{source_id}` |
| **Pull（拉）** | 对方只发布文件 / API，不会主动推 | `bundle_pull`（对方已是统一信封）· `http_pull`（任意 JSON/RSS + 字段映射） |
| **MCP（查询）** | 交互式检索历史情报 | `mcp_query`。**不是日更路径** |

**加一个源 = 写一个 YAML，不用改代码**（见 [`sources/`](sources/)）。
YAML 是唯一真相，启动时投影进数据库；UI 对这些源只读。

```yaml
# sources/hn.yaml —— 拉一个 JSON API，零代码
source_id: hn_frontpage
adapter: http_pull
schedule: { cron: "0 6 * * *" }
fetch:
  format: json          # json | rss
  url: https://hn.algolia.com/api/v1/search?tags=front_page
map:
  items: $.hits         # 路径表达式：$.a.b / $.a[0] / $.a[*].b
  external_id: $.objectID
  title: $.title
  url: $.url
```

## 文档

- [快速上手](docs/快速上手.md) — 接一个源并跑通一次
- [使用指南](docs/使用指南.md) — 完整配置与运维
- [部署](docs/部署.md) — VPS + Docker 部署、HTTPS(Caddy)、数据备份/恢复
