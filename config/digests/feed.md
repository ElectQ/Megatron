{#-
  推送文案模板 —— 「仅链接」形态（GitHub 关注流等页面型源）。
  不铺条目,只发一个入口:标题 + 一行统计 + 日刊链接。详情都在页面里。
  可用变量同 digest.md（title / date / ingest_total / day_url / must_see / recommend / trimmed）,
  这里只用到前四个。
-#}
⚡ {{ title }} · {{ date }}
今日 {{ ingest_total }} 条动态已汇总,点开看谁在关注什么。
{% if day_url %}

[📖 查看今日详情 →]({{ day_url }})
{% endif %}
