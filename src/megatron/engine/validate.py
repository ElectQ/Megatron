"""Validate an LLM response against its prompt's declared output_schema.

`PromptTemplate.output_schema` has always been stored, snapshotted — and never
checked. This checks it.

Validation failures are recorded, not fatal. The limits that actually matter
(how many things may interrupt you) are enforced structurally by
`bundle.enforce_caps`, which does not care what the model claims. So a model
that drifts on some optional field should not cost you the day's brief; the
error is surfaced on the run instead.
"""

from __future__ import annotations

from ..core.logging import get_logger

logger = get_logger(__name__)

MAX_ERRORS = 10


def validate_output(parsed: dict, schema: dict) -> list[str]:
    """Return human-readable schema violations. Empty schema means no opinion."""
    if not schema or not isinstance(parsed, dict):
        return []

    try:
        import jsonschema
    except ImportError:  # pragma: no cover - dependency is declared
        logger.warning("validate.jsonschema_missing")
        return []

    validator_cls = jsonschema.validators.validator_for(schema)
    try:
        validator_cls.check_schema(schema)
    except jsonschema.SchemaError as e:
        logger.warning("validate.bad_schema", error=str(e))
        return []

    errors = []
    for err in validator_cls(schema).iter_errors(parsed):
        path = ".".join(str(p) for p in err.absolute_path) or "(root)"
        errors.append(f"{path}: {err.message}")
        if len(errors) >= MAX_ERRORS:
            break
    return errors


__all__ = ["validate_output", "MAX_ERRORS"]
