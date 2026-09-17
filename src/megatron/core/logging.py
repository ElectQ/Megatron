from __future__ import annotations

import logging
import logging.handlers
import os
import sys

import structlog

# Docker writes these to /app/data (the mounted volume), so a run's logs outlive
# `docker compose down` — the container's stdout does not.
LOG_DIR = os.getenv("MEGATRON_LOG_DIR", "/app/data/logs")
LOG_FILE = "megatron.log"
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUPS = 5
LOG_FORMAT = os.getenv(
    "MEGATRON_LOG_FORMAT",
    "json" if os.getenv("MEGATRON_ENV", "").lower() in {"prod", "production"} else "console",
).lower()


def setup_logging(level: str = "INFO") -> None:
    numeric = getattr(logging, level.upper(), logging.INFO)

    # One handler writes stdout (what `docker compose logs` shows), one appends to
    # a rotating file. Both render the same event the same way.
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    file_handler = _file_handler()
    if file_handler is not None:
        handlers.append(file_handler)

    # ProcessorFormatter is what lets stdlib logging and structlog share these
    # handlers: structlog events go out through processor `wrap_for_formatter`,
    # stdlib records (uvicorn, sqlalchemy) through `foreign_pre_chain`.
    shared = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]
    renderer = structlog.processors.JSONRenderer() if LOG_FORMAT == "json" else structlog.dev.ConsoleRenderer()
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared,
    )
    for handler in handlers:
        handler.setFormatter(formatter)

    root = logging.getLogger()
    for handler in root.handlers:
        handler.close()
    root.handlers = handlers
    root.setLevel(numeric)

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.make_filtering_bound_logger(numeric),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def _file_handler() -> logging.Handler | None:
    """Rotating file sink. Returns None when the directory is not writable.

    A logging directory must never be the reason the app fails to start.
    """
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except OSError:
        return None

    handler = logging.handlers.RotatingFileHandler(
        os.path.join(LOG_DIR, LOG_FILE),
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUPS,
        encoding="utf-8",
    )
    return handler


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


def log_path() -> str:
    return os.path.join(LOG_DIR, LOG_FILE)
