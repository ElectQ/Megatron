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
    # Why the model stopped (`stop` / `length` / `content_filter`). `length` says
    # the answer was cut off, which is the difference between "returned nothing"
    # and "returned nothing we could use".
    finish_reason: str = ""


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
        # Cap each call so a hung upstream can't stall a run indefinitely.
        self.timeout = float(config.get("timeout", 120))

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
            "timeout": self.timeout,
        }
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if tools:
            kwargs["tools"] = tools
        if response_format:
            kwargs["response_format"] = response_format

        logger.info(
            "llm.chat.start",
            model=self.model,
            messages=len(messages),
            json_mode=bool(response_format),
        )
        try:
            resp = await litellm.acompletion(**kwargs)
        except Exception as e:
            logger.error("llm.chat.failed", model=self.model, error=str(e))
            raise

        choice = resp.choices[0]
        message = choice.message
        content = message.content or ""
        finish_reason = getattr(choice, "finish_reason", None) or ""
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
            finish_reason=finish_reason,
            content_chars=len(content),
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
            finish_reason=finish_reason,
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
                    repaired = _repair_truncated_json(candidate)
                    if repaired is not None:
                        return repaired
                    fallback = _fallback_extract(text)
                    if fallback is not None:
                        return fallback
                    raise ValueError("Malformed JSON object")
    raise ValueError("Unbalanced JSON braces")


def _repair_truncated_json(text: str) -> dict | None:
    """Recover the complete prefix of a truncated JSON object.

    The previous implementation counted every ``{`` and ``}``, including those
    inside a model-written summary or code snippet. A one-liner such as
    ``payload {abc`` therefore made recovery impossible. Here a small JSON-aware
    scanner tracks strings and escapes before deciding where a partial item can
    be cut. Every recovered result is marked ``_partial``: callers must never
    publish it as a complete daily tiering.
    """
    start = text.find("{")
    if start == -1:
        return _fallback_extract(text)
    candidate = text[start:].strip()
    stack, commas, in_string, invalid = _json_state(candidate)
    if invalid:
        return _fallback_extract(text)

    # A complete prefix may end at the input tail, before a comma (drop the
    # unfinished field/item), or before an unclosed object/array (drop that
    # unfinished structure). The latter rescues an item truncated halfway through
    # `one_liner: "payload {abc` without treating its brace as JSON syntax.
    cuts = ([] if in_string else [len(candidate)]) + list(reversed(commas))
    cuts += [pos for _, pos in reversed(stack)]
    seen: set[int] = set()
    for cut in cuts:
        if cut in seen:
            continue
        seen.add(cut)
        repaired = _close_json(candidate[:cut])
        if repaired is None:
            continue
        try:
            result = json.loads(repaired)
        except json.JSONDecodeError:
            continue
        if isinstance(result, dict):
            result["_partial"] = True
            return result

    # Strategy B: regex extract report_markdown only.
    return _fallback_extract(text)


def _json_state(text: str) -> tuple[list[tuple[str, int]], list[int], bool, bool]:
    """Return ``(unclosed_stack, commas, in_string, invalid)`` for JSON text."""
    stack: list[tuple[str, int]] = []
    commas: list[int] = []
    in_string = False
    escape = False
    invalid = False
    pairs = {"}": "{", "]": "["}

    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append((ch, i))
        elif ch in "}]":
            if not stack or stack[-1][0] != pairs[ch]:
                invalid = True
                break
            stack.pop()
        elif ch == ",":
            commas.append(i)
    return stack, commas, in_string, invalid


def _close_json(prefix: str) -> str | None:
    """Close a complete JSON prefix, or return None when its tail is partial."""
    prefix = prefix.rstrip().rstrip(",").rstrip()
    if not prefix or prefix.endswith(":"):
        return None
    stack, _, in_string, invalid = _json_state(prefix)
    if in_string or invalid:
        return None
    closers = ("}" if opener == "{" else "]" for opener, _ in reversed(stack))
    return prefix + "".join(closers)


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
