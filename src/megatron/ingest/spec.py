"""Declarative source specs.

A source is described by one YAML file under the sources directory (see
`MEGATRON_SOURCES_DIR`). The file is the source of truth; the `source_configs`
table is a projection of it, refreshed on boot and on demand.

Minimal push source (a collector POSTs to us):

    source_id: twitter_security_list
    display_name: Twitter 安全 List
    kind: twitter_list
    adapter: http_push
    audience: [personal]
    schedule_expect:
      timezone: Asia/Shanghai
      collect_by: "06:00"
      sla_minutes: 90

Raw-HTTP pull source (we poll a URL and map its payload — no code required):

    source_id: hn_frontpage
    adapter: http_pull
    schedule:
      cron: "0 6 * * *"
    fetch:
      format: json
      url: https://hn.algolia.com/api/v1/search?tags=front_page
      headers:
        Authorization: "Bearer ${HN_TOKEN}"   # resolved from the environment
    map:
      items: $.hits
      external_id: $.objectID
      title: $.title
      url: $.url
      published_at: $.created_at
      metrics:
        like_count: $.points
"""

from __future__ import annotations

import os
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SLUG_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

ADAPTERS = ("http_push", "http_pull", "git_pull", "mcp_query", "native")
AUDIENCES = ("personal", "public")

Adapter = Literal["http_push", "http_pull", "git_pull", "mcp_query", "native"]


def expand_env(value: str) -> str:
    """Replace ${VAR} with the environment value.

    Lets a spec reference an API token without the token itself living in the
    file. An unset variable expands to "" rather than raising, so a source with
    a missing credential fails at fetch time with an HTTP error we can log —
    instead of taking the whole boot-time sync down with it.
    """
    return _ENV_RE.sub(lambda m: os.getenv(m.group(1), ""), value)


class ScheduleExpect(BaseModel):
    """When this source is expected to have delivered, for the arrival check."""

    model_config = ConfigDict(extra="forbid")

    timezone: str = "Asia/Shanghai"
    collect_by: str = ""  # "HH:MM" local time; empty = no SLA, never "late"
    sla_minutes: int = 0

    @field_validator("collect_by")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        if v and not re.match(r"^\d{2}:\d{2}$", v):
            raise ValueError("collect_by must be HH:MM")
        return v


class Schedule(BaseModel):
    """When Megatron itself polls this source (http_pull / git_pull only)."""

    model_config = ConfigDict(extra="forbid")

    cron: str = ""  # empty = not polled automatically


class FetchSpec(BaseModel):
    """How to reach a raw-HTTP source."""

    model_config = ConfigDict(extra="forbid")

    format: Literal["json", "rss"] = "json"
    url: str
    method: Literal["GET", "POST"] = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    params: dict[str, str] = Field(default_factory=dict)
    body: dict[str, Any] | None = None
    timeout: float = 20.0

    def resolved_url(self) -> str:
        return expand_env(self.url)

    def resolved_headers(self) -> dict[str, str]:
        return {k: expand_env(v) for k, v in self.headers.items()}

    def resolved_params(self) -> dict[str, str]:
        return {k: expand_env(v) for k, v in self.params.items()}


class MapSpec(BaseModel):
    """Path expressions turning a fetched payload into Items.

    `items` is evaluated against the response root and must yield a list. Every
    other expression is evaluated against one element of that list. A value that
    does not start with `$` is used verbatim as a constant.
    """

    model_config = ConfigDict(extra="forbid")

    items: str = "$"

    external_id: str = "$.id"
    title: str = ""
    content: str = ""
    url: str = ""
    author: str = ""
    author_name: str = ""
    language: str = ""
    published_at: str = ""

    tags: str = ""
    links: str = ""
    metrics: dict[str, str] = Field(default_factory=dict)


class SourceSpec(BaseModel):
    """One logical source, as declared in YAML."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    display_name: str = ""
    kind: str = ""
    adapter: Adapter = "http_push"
    audience: list[Literal["personal", "public"]] = Field(default_factory=lambda: ["personal"])
    enabled: bool = True

    schedule: Schedule = Field(default_factory=Schedule)
    schedule_expect: ScheduleExpect = Field(default_factory=ScheduleExpect)

    fetch: FetchSpec | None = None
    map: MapSpec | None = None

    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError(
                f"source_id '{v}' must match {SLUG_RE.pattern} "
                "(lowercase, starts with a letter, 2-32 chars)"
            )
        return v

    @model_validator(mode="after")
    def _adapter_requirements(self) -> SourceSpec:
        if self.adapter == "http_pull":
            if self.fetch is None:
                raise ValueError("adapter 'http_pull' requires a `fetch:` block")
            if self.map is None and self.fetch.format == "json":
                raise ValueError("adapter 'http_pull' with format json requires a `map:` block")
        if self.adapter == "git_pull" and not self.config.get("repo_url"):
            raise ValueError("adapter 'git_pull' requires config.repo_url")
        return self

    @property
    def audience_scalar(self) -> str:
        """Collapse the contract's list into the column's scalar."""
        if set(self.audience) >= {"personal", "public"}:
            return "both"
        return self.audience[0] if self.audience else "personal"

    def db_config(self) -> dict[str, Any]:
        """The adapter-specific blob stored on the registry row."""
        cfg = dict(self.config)
        if self.fetch is not None:
            cfg["fetch"] = self.fetch.model_dump()
        if self.map is not None:
            cfg["map"] = self.map.model_dump()
        if self.schedule.cron:
            cfg["cron"] = self.schedule.cron
        return cfg


__all__ = [
    "ADAPTERS",
    "AUDIENCES",
    "SLUG_RE",
    "FetchSpec",
    "MapSpec",
    "Schedule",
    "ScheduleExpect",
    "SourceSpec",
    "expand_env",
]
