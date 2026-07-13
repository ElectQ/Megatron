{#-
  推送文案模板 —— 「分档」形态（推特安全流）。
  这是产品文案,不是框架代码:改推送长什么样就改这个文件。

  引擎已经把 LLM 分析出的 bundle 备好成下面这些变量喂进来:
    title          源的展示名（如 推特安全流）
    date           日期 YYYY-MM-DD
    ingest_total   当天入库条数
    day_url        日刊页链接（带不可猜 token）
    must_see       必看条目 [{title, why, url}]（已按重要性排序、已裁到上限）
    recommend      推荐条目 [{title, why, url}]（已按字数预算裁好）
    trimmed        因字数超限被折叠掉的推荐条数（>0 时提示「另有 N 条」）

  只负责布局和文案；数量/字数的装配逻辑在引擎里（doorbell.py）。
-#}
⚡ {{ title }} · {{ date }}
入库 {{ ingest_total }} · 必看 {{ must_see | length }} · 推荐 {{ recommend | length }}
{% if must_see %}

🔴 **必看**
{% for it in must_see %}
{{ loop.index }}. **{{ it.title }}**
{% if it.why %}
   {{ it.why }}
{% endif %}
   [原文 ↗]({{ it.url }})
{% endfor %}
{% else %}

今日无必看条目。
{% endif %}
{% if recommend %}

🟡 **推荐**
{% for it in recommend %}
- {{ it.title }} [原文 ↗]({{ it.url }})
{% endfor %}
{% endif %}
{% if trimmed %}
- …另有 {{ trimmed }} 条，见详情
{% endif %}
{% if day_url %}

——
[📖 查看今日详情 →]({{ day_url }})
{% endif %}
