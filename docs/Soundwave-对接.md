# Soundwave 对接说明

**当前状态：Soundwave 不需要任何改动。** 这份文档说明为什么，以及如果你想更实时该怎么改。

## 现在是怎么接的

Soundwave 每天 `21:13 UTC`（05:13 北京）跑 crawl，把日包提交到自己仓库：

```
bundles/index.json          ← {source_id, latest, watermark, days:[{date, count, sha256}]}
bundles/YYYY-MM-DD.json     ← 日包
```

而 `bundles/YYYY-MM-DD.json` **本身就是** Megatron 的 `schema_version: 1` 统一信封：

```json
{
  "schema_version": 1,
  "source_id": "twitter_security_list",
  "collect_date": "2026-07-11",
  "producer": {"name": "soundwave", "version": "1.0.0", "run_id": "..."},
  "items": [
    {"external_id": "...", "content": "...", "url": "...",
     "author": "0x534c", "author_name": "...",
     "published_at": "...", "collected_at": "...",
     "tags": ["list:sec_list"], "links": [...],
     "metrics": {"like_count": 3, "retweet_count": 1, "reply_count": 0, "view_count": 222},
     "flags": {"is_retweet": false, "is_quote": false}}
  ]
}
```

字段名、`source_id`、去重键全都对得上。所以 Megatron 直接拉这个文件就能入库，
**不需要任何字段映射**。

Megatron 侧：`sources/twitter_security_list.yaml`，`adapter: bundle_pull`，
每天 `22:30 UTC`（06:30 北京）去 `index.json` 看有没有新日期。

- 只拉 watermark 之后的日期（增量）。
- 校验 `sha256`：`index.json` 和 `<date>.json` 是 CDN 上两个独立对象，可能被读到不一致的版本。
  对不上 → 跳过、等下次轮询，而不是吃进一个残缺的日包。
- 去重键 `(source_id, external_id)`，重复拉安全。

> Soundwave 的 README 说得很清楚：`bundles/` 是与 Megatron 的**契约面**，
> `data/` 是内部原始层、不该被直接消费。Megatron 只读 `bundles/`。

## 如果想更实时：改成 HTTP Push

`bundle_pull` 有最长 ~1.5 小时的延迟（cron 间隔）。想让 crawl 一跑完就推过来：

### 1. Megatron 侧：改 YAML

```yaml
# sources/twitter_security_list.yaml
adapter: http_push        # 从 bundle_pull 改过来
# 删掉 schedule: 和 config.index_url / max_days / verify_sha256
```

然后 `megatron sources sync`。

### 2. Soundwave 侧：在 crawl.yml 里加一步

```yaml
- name: Push bundle to Megatron
  if: success()
  env:
    MEGATRON_INGEST_URL: ${{ secrets.MEGATRON_INGEST_URL }}
    MEGATRON_INGEST_TOKEN: ${{ secrets.MEGATRON_INGEST_TOKEN }}
  run: |
    DATE=$(TZ=Asia/Shanghai date +%Y-%m-%d)
    BUNDLE="bundles/${DATE}.json"
    test -f "$BUNDLE" || { echo "::error::no bundle for ${DATE}"; exit 1; }
    curl -fsS -X POST "$MEGATRON_INGEST_URL" \
      -H "Authorization: Bearer $MEGATRON_INGEST_TOKEN" \
      -H "Content-Type: application/json" \
      --data-binary @"$BUNDLE"
```

**注意 `curl -f`**：没有它，Megatron 返回 4xx/5xx 时这一步仍然算成功，
就会出现"爬成功了，但 Core 根本没收到"而没人发现。

### 3. Secrets

| Secret | 值 |
|---|---|
| `MEGATRON_INGEST_URL` | `https://<host>/api/ingest/twitter_security_list` |
| `MEGATRON_INGEST_TOKEN` | Megatron 的 `MEGATRON_INGEST_TOKEN`（首启自动生成在 `/app/data/.ingest_token`） |

Megatron 的 `GET /api/admin/sources/twitter_security_list/curl` 会直接吐出可粘贴的 curl。

### 两种方式对比

| | bundle_pull（当前） | http_push |
|---|---|---|
| Soundwave 改动 | **零** | 加一步 + 2 个 secret |
| 延迟 | ≤ cron 间隔 | 秒级 |
| Megatron 需要公网可达 | 否 | **是** |
| 完整性校验 | sha256 | 无（但有 TLS + token） |
| Soundwave 挂了 | Megatron 拉不到 → 源标 `missing` | 同左 |

两种都是幂等的，甚至可以**同时开**（先 push，pull 作兜底）——重复的会被去重键挡掉。

## Megatron 不做什么

- 不持有 `TWITTER_AUTH_TOKEN` / `TWITTER_CT0` 去自己爬。
- 不消费 `data/`（那是 Soundwave 的内部层）。
- 不要求 Soundwave 填 `tier` / `why_for_me` —— 那是分析层的输出，
  collector 只描述事实。
