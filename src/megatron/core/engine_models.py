from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LLMProvider(Base):
    __tablename__ = "llm_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    model: Mapped[str] = mapped_column(String(128))
    api_base: Mapped[str] = mapped_column(String(256), default="")
    api_key: Mapped[str] = mapped_column(Text, default="")
    temperature: Mapped[float] = mapped_column(Float, default=0.7)
    max_tokens: Mapped[int] = mapped_column(Integer, default=8192)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    template: Mapped[str] = mapped_column(Text)
    output_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AnalysisModule(Base):
    __tablename__ = "analysis_modules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")

    source: Mapped[str] = mapped_column(String(32), default="twitter")
    source_ref: Mapped[str] = mapped_column(String(64), default="")
    filter_config: Mapped[dict] = mapped_column(JSON, default=dict)

    prompt_template_id: Mapped[int] = mapped_column(ForeignKey("prompt_templates.id"), index=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("llm_providers.id"), index=True)

    agent_backend: Mapped[str] = mapped_column(String(32), default="none")
    tools_config: Mapped[dict] = mapped_column(JSON, default=list)
    webhook_channel_ids: Mapped[list] = mapped_column(JSON, default=list)
    schedule_cron: Mapped[str] = mapped_column(String(64), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ModuleChannel(Base):
    __tablename__ = "module_channels"

    module_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_modules.id", ondelete="CASCADE"),
        primary_key=True,
    )
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("webhook_channels.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    module_id: Mapped[int] = mapped_column(ForeignKey("analysis_modules.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)

    input_count: Mapped[int] = mapped_column(Integer, default=0)
    input_item_ids: Mapped[list] = mapped_column(JSON, default=list)

    module_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    prompt_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    provider_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    rendered_prompt_hash: Mapped[str] = mapped_column(String(64), default="")

    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")

    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    duration_sec: Mapped[float] = mapped_column(Float, default=0.0)
    tool_calls: Mapped[list] = mapped_column(JSON, default=list)

    triggered_by: Mapped[str] = mapped_column(String(32), default="manual")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text, default="")
    display_name: Mapped[str] = mapped_column(String(128), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class WebhookChannel(Base):
    __tablename__ = "webhook_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class DeliveryLog(Base):
    __tablename__ = "delivery_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("webhook_channels.id"), index=True)
    channel_name: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="pending")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class DigestTemplate(Base):
    """A webhook message template — the `digest_style` a task selects.

    Seeded from config/digests/*.md (file = seed), then editable in the admin UI
    (DB = truth after seed), exactly like PromptTemplate.
    """

    __tablename__ = "digest_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    style: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    body: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Policy(Base):
    """Global filtering policy — a single row. Seeded from config/policy.yaml,
    then editable in the admin UI."""

    __tablename__ = "policy"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    caps: Mapped[dict] = mapped_column(JSON, default=dict)
    politics_blocklist: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SystemSetting(Base):
    """Runtime system settings — a single row, editable in the admin UI.

    `base_url` is the address readers reach this install at; every pushed link
    (day page, 详情) is built from it. Seeded from MEGATRON_BASE_URL at first boot
    so an operator can set the real domain in the UI instead of redeploying.
    """

    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    base_url: Mapped[str] = mapped_column(String(256), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


__all__ = [
    "LLMProvider",
    "PromptTemplate",
    "AnalysisModule",
    "ModuleChannel",
    "AnalysisRun",
    "User",
    "WebhookChannel",
    "DeliveryLog",
    "DigestTemplate",
    "Policy",
    "SystemSetting",
]
