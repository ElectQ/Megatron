{#-
  推送文案模板 —— 「仅链接」形态（GitHub 关注流等页面型源）。
  不铺条目,只发一个入口:标题 + 一行统计 + 日刊链接。详情都在页面里。
  可用变量:title / date / ingest_total / day_url / public_url / must_see / recommend / trimmed。
  这里用前四个 + public_url。

  链接优先用 public_url —— 公开前台的当日页(无 token、可分享给钉钉群里所有人、可被搜索引擎收录);
  当天没有可公开内容时 public_url 为空,自动回落到私有的 day_url(带不可猜 token 的个人页)。
-#}
⚡ {{ title }} · {{ date }}
今日 {{ ingest_total }} 条动态已汇总,点开看谁在关注什么。
{% set link = public_url or day_url %}
{% if link %}

[📖 查看今日详情 →]({{ link }})
{% endif %}
