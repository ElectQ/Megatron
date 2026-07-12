"""Output schemas and prompt identifiers — the engine-contract half of the prompts.

The prompt *bodies* (the product copy) live in `config/prompts/*.md` and are seeded
into the DB by `megatron.profile.loader`. What stays here is the structural
contract each prompt validates against: the JSON-Schema for its output, and the
canonical name/display constants other code references by identity.

A prompt file declares `output_schema: <name>` in its frontmatter; `SCHEMAS`
resolves that name to the schema object below.
"""

from __future__ import annotations

# Canonical prompt names — referenced by identity elsewhere (CLI `use-day-bundle`,
# task specs, tests), so they stay in code even though the bodies moved to files.
DEFAULT_PROMPT_NAME = "daily_security_briefing"
DEFAULT_PROMPT_DISPLAY = "推特安全信息流简报"
DAILY_INTEL_V1_NAME = "daily_intel_v1"
DAILY_INTEL_V1_DISPLAY = "每日情报分级（门铃 / 日刊）"
GITHUB_RADAR_V1_NAME = "github_radar_v1"
GITHUB_RADAR_V1_DISPLAY = "GitHub 关注流分级（仅日刊页）"


DEFAULT_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "report_markdown": {"type": "string", "description": "分档简报 Markdown"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": ["vuln", "tool", "research", "threat", "incident", "advisory"],
                    },
                    "tier": {
                        "type": "string",
                        "enum": ["must", "recommended", "quick"],
                        "description": "必看/推荐/速览",
                    },
                    "freshness": {
                        "type": "string",
                        "enum": ["new", "reshare"],
                        "description": "new=今日首发 / reshare=今日讨论的旧成果",
                    },
                    "cve": {"type": "string"},
                    "summary": {"type": "string"},
                    "links": {"type": "array", "items": {"type": "string"}},
                    "source_url": {"type": "string"},
                    "author": {"type": "string"},
                    "artifact": {
                        "type": "string",
                        "enum": ["repo", "article", "poc", "video", "none"],
                    },
                },
                "required": ["title", "category", "tier", "freshness", "summary", "source_url"],
            },
        },
    },
    "required": ["report_markdown", "items"],
}


DAILY_INTEL_V1_SCHEMA = {
    "type": "object",
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                # A `drop` needs nothing but its verdict — writing a one-liner and
                # tags for something being thrown away is wasted output, and on a
                # 144-item day two thirds of the answer is drops.
                "required": ["external_id", "source_id", "tier"],
                "properties": {
                    "external_id": {"type": "string"},
                    "source_id": {"type": "string"},
                    "tier": {
                        "type": "string",
                        "enum": [
                            "must_see_push",
                            "must_see_page",
                            "recommend",
                            "skim",
                            "drop",
                        ],
                    },
                    "one_liner": {"type": "string"},
                    "why_for_me": {"type": "string"},
                    "actionability": {
                        "type": "string",
                        "enum": ["none", "read", "watch", "try"],
                    },
                    "topics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 4,
                    },
                    "scores": {"type": "object"},
                    # Whether this item may appear on the public blog. Default
                    # false (private); only set true for已公开/客观 information.
                    "public": {"type": "boolean"},
                },
                # What a card owes the reader scales with how prominently it is
                # rendered. A skim line is a title and a chip; demanding a
                # personal "why this matters to you" for it produces filler, and
                # a schema that flags honest work is a schema you learn to ignore.
                "allOf": [
                    {
                        "if": {
                            "properties": {
                                "tier": {"enum": ["must_see_push", "must_see_page", "recommend"]}
                            },
                            "required": ["tier"],
                        },
                        "then": {
                            "required": ["one_liner", "why_for_me", "topics"],
                            "properties": {"topics": {"minItems": 2}},
                        },
                    },
                    {
                        "if": {"properties": {"tier": {"const": "skim"}}, "required": ["tier"]},
                        "then": {
                            "required": ["one_liner", "topics"],
                            "properties": {"topics": {"minItems": 1}},
                        },
                    },
                ],
            },
        },
        "push_item_ids": {"type": "array", "items": {"type": "string"}},
    },
}


# Name → schema. A prompt file's `output_schema:` frontmatter is resolved through
# this. github_radar shares the intel schema (same day-page/bundle output shape).
SCHEMAS = {
    "daily_security_briefing": DEFAULT_OUTPUT_SCHEMA,
    "daily_intel_v1": DAILY_INTEL_V1_SCHEMA,
    "github_radar_v1": DAILY_INTEL_V1_SCHEMA,
}


def schema_for(name: str) -> dict:
    """Resolve a prompt's `output_schema` name to its schema. Unknown → no schema."""
    return SCHEMAS.get(name or "", {})


__all__ = [
    "DAILY_INTEL_V1_DISPLAY",
    "DAILY_INTEL_V1_NAME",
    "DAILY_INTEL_V1_SCHEMA",
    "DEFAULT_OUTPUT_SCHEMA",
    "DEFAULT_PROMPT_DISPLAY",
    "DEFAULT_PROMPT_NAME",
    "GITHUB_RADAR_V1_DISPLAY",
    "GITHUB_RADAR_V1_NAME",
    "SCHEMAS",
    "schema_for",
]
