from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ChatResponse:
    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    tool_calls: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)


class LLMProvider:
    """LiteLLM-backed provider. Reads api_key directly from config (plaintext DB).

    Config keys: model, api_key, api_base (optional), temperature, max_tokens.
    """

    def __init__(self, config: dict):
        self.model = config["model"]
        self._api_key = config.get("api_key", "") or None
        self.api_base = config.get("api_base", "") or None
        self.temperature = float(config.get("temperature", 0.7))
        self.max_tokens = int(config.get("max_tokens", 4096))

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        response_format: dict | None = None,
    ) -> ChatResponse:
        import litellm

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "api_key": self._api_key,
        }
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if tools:
            kwargs["tools"] = tools
        if response_format:
            kwargs["response_format"] = response_format

        logger.info("llm.chat.start", model=self.model, messages=len(messages))
        try:
            resp = await litellm.acompletion(**kwargs)
        except Exception as e:
            logger.error("llm.chat.failed", model=self.model, error=str(e))
            raise

        choice = resp.choices[0]
        message = choice.message
        content = message.content or ""
        usage = getattr(resp, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
        completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0

        tool_calls = []
        raw_tcs = getattr(message, "tool_calls", None)
        if raw_tcs:
            for tc in raw_tcs:
                fn = getattr(tc, "function", None)
                if not fn:
                    continue
                tool_calls.append(
                    {
                        "id": getattr(tc, "id", ""),
                        "type": getattr(tc, "type", "function"),
                        "function": {
                            "name": getattr(fn, "name", ""),
                            "arguments": getattr(fn, "arguments", "{}"),
                        },
                    }
                )

        cost = 0.0
        try:
            cost = litellm.completion_cost(completion_response=resp) or 0.0
        except Exception:
            pass

        logger.info(
            "llm.chat.done",
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost=round(cost, 6),
            tool_calls=len(tool_calls),
        )
        return ChatResponse(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=float(cost),
            tool_calls=tool_calls,
            raw={"model": self.model},
        )


_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)
# 正则提取 report_markdown(即使 JSON 被截断也能拿到)
_MD_FIELD_RE = re.compile(r'"report_markdown"\s*:\s*"((?:[^"\\]|\\.)*)"', re.DOTALL)
_ITEMS_FIELD_RE = re.compile(r'"items"\s*:\s*(\[.*\])\s*[,}]\s*$', re.DOTALL)


def parse_json_response(content: str) -> Any:
    """Extract a JSON object from an LLM response.

    Strategy (in order):
    1. Find the LAST ```json code block
    2. Fall back to direct json.loads on stripped text
    3. Brace-matching extraction
    4. On all failures, raise ValueError (caller decides fallback)
    """
    matches = list(_FENCE_RE.finditer(content))
    for m in reversed(matches):
        candidate = m.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            repaired = _repair_truncated_json(candidate)
            if repaired is not None:
                return repaired

    text = content.strip()
    for fence in ("```json\n", "```json", "```\n", "```"):
        if text.startswith(fence):
            text = text[len(fence) :]
            break
    if text.endswith("```"):
        text = text[: -len("```")]
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    repaired = _repair_truncated_json(text)
    if repaired is not None:
        return repaired

    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found")

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    return _repair_truncated_json(candidate) or (_fallback_extract(text) or {})
    raise ValueError("Unbalanced JSON braces")


def _repair_truncated_json(text: str) -> dict | None:
    """Attempt to repair a truncated JSON by closing open structures.

    Tries (in order):
    1. Strip trailing partial field + close arrays/objects
    2. Extract report_markdown via regex if structure is hopeless
    Returns dict or None.
    """
    # Strategy A: close open braces/brackets
    candidate = text.strip()
    if not candidate.startswith("{"):
        candidate = "{" + candidate.split("{", 1)[1] if "{" in candidate else candidate

    # Count unclosed structures (rough, ignores strings for speed)
    open_braces = candidate.count("{") - candidate.count("}")
    open_brackets = candidate.count("[") - candidate.count("]")

    # Remove trailing partial content after last complete field
    last_quote_colon = candidate.rfind('": "')
    last_comma = candidate.rfind(", ")
    last_close = max(candidate.rfind("}"), candidate.rfind("]"))
    if last_quote_colon > last_close and last_quote_colon > last_comma:
        # Truncated mid-string: cut back to last complete value
        cut_point = candidate.rfind('", ', 0, last_quote_colon)
        if cut_point > 0:
            candidate = candidate[: cut_point + 2]

    # Close open structures
    for _ in range(max(open_brackets, 0)):
        candidate += "]"
    for _ in range(max(open_braces, 0)):
        candidate += "}"

    try:
        result = json.loads(candidate)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # Strategy B: regex extract report_markdown only
    return _fallback_extract(text)


def _fallback_extract(text: str) -> dict | None:
    """Last resort: regex extract report_markdown field directly.

    If the JSON is hopelessly broken but report_markdown string is intact,
    pull it out so at least the push can go out.
    """
    m = _MD_FIELD_RE.search(text)
    if not m:
        return None
    raw_md = m.group(1)
    # Unescape JSON string escapes
    try:
        md = json.loads(f'"{raw_md}"')
    except Exception:
        md = raw_md.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
    return {"report_markdown": md, "items": [], "_partial": True}


__all__ = ["LLMProvider", "ChatResponse", "parse_json_response"]
