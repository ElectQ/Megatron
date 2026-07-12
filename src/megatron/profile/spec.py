"""Declarative specs for the product profile — prompts and analysis tasks.

Same shape as `ingest/spec.py:SourceSpec` (pydantic, `extra="forbid"`), so a typo
in a config file is a loud parse error, not a silent misconfiguration.

These are *seed* specs, not authoritative ones: the loader creates a DB row only
when none by that name exists, so runtime edits in the admin UI are never
overwritten. The file is the starting point; the DB is the truth after boot.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


class PromptSpec(BaseModel):
    """A prompt template file: YAML frontmatter + Jinja/markdown body."""

    model_config = ConfigDict(extra="forbid")

    name: str
    display_name: str = ""
    output_schema: str = ""  # resolved to a schema object via builtin.schema_for
    body: str

    @field_validator("name")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not NAME_RE.match(v):
            raise ValueError(f"prompt name '{v}' must match {NAME_RE.pattern}")
        return v


class TaskSpec(BaseModel):
    """An analysis task file — mirrors the editable fields of AnalysisModule.

    `prompt` / `provider` / `channels` are *names*; the loader resolves them to
    ids. `channels: []` means page-only (never delivered).
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    source: str
    prompt: str
    provider: str = "deepseek"
    schedule_cron: str = ""
    channels: list[str] = Field(default_factory=list)
    enabled: bool = True
    filter_config: dict = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not NAME_RE.match(v):
            raise ValueError(f"task name '{v}' must match {NAME_RE.pattern}")
        return v
