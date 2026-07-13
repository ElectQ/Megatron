from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ItemRecord(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    source_ref: Mapped[str] = mapped_column(String(64), default="", index=True)

    title: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String(128), default="", index=True)
    author_name: Mapped[str] = mapped_column(String(256), default="")
    language: Mapped[str] = mapped_column(String(16), default="")

    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    # Soundwave 采集日(对应 data/YYYY-MM-DD 目录名),便于按天筛选/统计
    collect_date: Mapped[str] = mapped_column(String(16), default="", index=True)

    is_retweet: Mapped[bool] = mapped_column(default=False)
    is_quote: Mapped[bool] = mapped_column(default=False)

    tags: Mapped[list] = mapped_column(JSON, default=list)
    links: Mapped[list] = mapped_column(JSON, default=list)
    media: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)

    importance_score: Mapped[float] = mapped_column(default=0.0)
    analysis_state: Mapped[str] = mapped_column(String(32), default="new", index=True)

    __table_args__ = (
        Index("ux_items_unique", "source", "item_id", unique=True),
        Index("ix_items_date", func.date(published_at)),
    )

    def __repr__(self) -> str:
        return f"<ItemRecord {self.source}:{self.item_id}>"


class IngestLog(Base):
    __tablename__ = "ingest_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    source_ref: Mapped[str] = mapped_column(String(64), default="")
    date: Mapped[str] = mapped_column(String(16), default="")
    mode: Mapped[str] = mapped_column(String(16))
    ingested: Mapped[int] = mapped_column(Integer, default=0)
    duplicated: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class PullState(Base):
    """Watermark tracking last-pulled date per source.

    Used by GitPuller to do incremental pulls: each run reads last_date and
    only ingests dates strictly after it. If the row is missing (cold start),
    puller falls back to full ingestion.
    """

    __tablename__ = "pull_state"

    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_date: Mapped[str] = mapped_column(String(16), default="")
    last_pull_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class SourceConfig(Base):
    """Registry row for one logical source.

    `name` doubles as the spec's `source_id`: it is already the de-facto primary
    key across the runner, the module editor and `items.source`, so introducing a
    separate `source_id` column would create two sources of truth.

    Rows whose `managed_by == "yaml"` are projections of a file under the sources
    directory and are read-only in the UI; the file is the truth.
    """

    __tablename__ = "source_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source_type: Mapped[str] = mapped_column(String(32), index=True)
    # "native" | "mcp" — legacy discriminator, superseded by `adapter`. Still
    # written so the existing MCP admin API keeps working; removed in Phase 1.
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    # adapter-specific: {plugin_name, mcp_server_id, repo_url, fetch, map, ...}
    enabled: Mapped[bool] = mapped_column(default=True)
    last_sync_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    display_name: Mapped[str] = mapped_column(String(128), default="")
    kind: Mapped[str] = mapped_column(String(32), default="")
    # free-form: twitter_list | github_release | rss | ...

    adapter: Mapped[str] = mapped_column(String(16), default="native", index=True)
    # http_push | http_pull | git_pull | mcp_query | native
    # Indexed because the scheduler filters on it to decide what to poll.

    audience: Mapped[str] = mapped_column(String(16), default="personal")
    # personal | public | both.
    # Deliberate deviation from the spec, which models this as a list: there is
    # never more than one value in Phase 0 and querying a JSON array in SQLite is
    # painful. The API serialises it back to a list to honour the contract.

    public_redact: Mapped[bool] = mapped_column(default=False)
    # When public, strip the fields that name who curated an item (author + the
    # raw content that embeds the name). See SourceSpec.public_redact. Synced from
    # YAML like audience; the token-gated day page ignores it.

    schedule_expect: Mapped[dict] = mapped_column(JSON, default=dict)
    # {timezone, collect_by, sla_minutes} — drives the daily arrival check.

    managed_by: Mapped[str] = mapped_column(String(16), default="db", index=True)
    # "yaml" (file is the truth, UI read-only) | "db" (created via the UI/API)


class MCPServer(Base):
    """MCP server connection registry.

    Stores connection details for MCP servers that provide data sources.
    """

    __tablename__ = "mcp_servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    server_url: Mapped[str] = mapped_column(String(512))
    # SSE endpoint URL or command for stdio
    transport: Mapped[str] = mapped_column(String(16), default="sse")
    # "sse" | "stdio"
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    # List of available resources/tools from MCP server
    status: Mapped[str] = mapped_column(String(16), default="disconnected")
    # "connected" | "disconnected" | "error"
    last_error: Mapped[str] = mapped_column(Text, default="")
    last_connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


__all__ = ["Base", "ItemRecord", "IngestLog", "PullState", "SourceConfig", "MCPServer"]
