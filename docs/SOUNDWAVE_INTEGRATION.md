# Soundwave 集成指南

Megatron 从 Soundwave 获取数据有两种方式：**推模式（主）** 和 **拉模式（兜底）**。

## 方式一：拉模式（零改动，推荐起步）

Megatron 定时 `git clone` Soundwave 仓库并导入数据。无需改动 Soundwave。

```bash
# .env 中配置
SOUNDWAVE_REPO_URL=https://github.com/ElectQ/Soundwave.git

# 或通过 CLI 手动拉取
megatron pull --repo https://github.com/ElectQ/Soundwave.git
```

拉模式每 6 小时自动执行一次（APScheduler），也支持手动触发。

## 方式二：推模式（实时，推荐生产）

Soundwave 抓取完成后主动 POST 数据到 Megatron，秒级送达。

### 步骤 1：获取 Megatron 的 ingest token

```bash
megatron gentoken
# 输出示例:
# INGEST_TOKEN=aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789
```

将生成的 token 写入 Megatron 的 `.env`。

### 步骤 2：在 Soundwave 的 GitHub repo 添加 secrets

进入 Soundwave 仓库 → Settings → Secrets and variables → Actions → New repository secret：

| Secret 名 | 值 |
|---|---|
| `MEGATRON_URL` | `https://megatron.yourdomain.com` |
| `MEGATRON_TOKEN` | 步骤 1 生成的 INGEST_TOKEN |

### 步骤 3：修改 Soundwave 的 `.github/workflows/crawl.yml`

在 `Commit data` 步骤之后添加：

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
              || echo "::warning::Push to Megatron failed (data still in git)"
          done
```

> **注意**：推送失败不会阻断 Soundwave 的 workflow（用 `::warning::` 而非 `exit 1`），因为数据已 commit 到 git，拉模式兜底也能补上。

### 验证

Soundwave 下次定时运行后，Megatron 的 `/ui/items` 页面应出现新数据，ingest 端点返回 `{"ingested": N, "duplicated": M}`。

## 数据格式契约

Megatron 的 `/api/ingest/twitter` 接收 Soundwave 的原始 JSON：

```json
{
  "date": "2026-06-16",
  "list_id": "1748402774835134821",
  "list_name": "sec_list",
  "crawled_at": "2026-06-16T15:14:56.646933+00:00",
  "count": 1,
  "tweets": [
    {
      "id": "2066900145321472382",
      "author_handle": "0xTriboulet",
      "content": "...",
      "url": "https://x.com/...",
      "published_at": "2026-06-16 15:06:10+00:00",
      "collected_at": "2026-06-16 15:14:56+00:00",
      "like_count": 5,
      "retweet_count": 2,
      ...
    }
  ]
}
```

## 幂等保证

推和拉可同时运行，`(source, item_id)` 唯一约束确保重复推送不会产生重复数据。重复推送的条目计入 `duplicated` 计数。
