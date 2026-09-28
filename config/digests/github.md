{#-
  GitHub 关注流推送：只发必看 / 推荐仓库。
  title 是仓库介绍，url 是原仓库地址；详情链接保留完整日刊。
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
{% if it.topics %}
   `{{ it.topics | join('` `') }}`
{% endif %}
   [查看仓库 ↗]({{ it.url }})
{% endfor %}
{% else %}

今日无必看仓库。
{% endif %}
{% if recommend %}

🟡 **推荐**
{% for it in recommend %}
- **{{ it.title }}**{% if it.topics %} · `{{ it.topics | join('` `') }}`{% endif %}
  [查看仓库 ↗]({{ it.url }})
{% endfor %}
{% endif %}
{% if trimmed %}
- …另有 {{ trimmed }} 条，见详情
{% endif %}
{% set link = public_url or day_url %}
{% if link %}

——
[📖 查看今日详情 →]({{ link }})
{% endif %}
