"""Structured logging via structlog.

Use this from every module:

    from core.logging import get_logger
    log = get_logger(__name__)
    log.info("order_submitted", order_id=order.id, broker="alpaca")

Outputs JSON in production (``LOG_FORMAT=json``) and a coloured console renderer in
dev. A redaction processor scrubs known secret keys before they're rendered, so even
an accidental ``log.info("...", api_secret=k)`` won't leak.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor

_SECRET_KEYS = frozenset(
    {
        "api_key",
        "api_secret",
        "apikey",
        "secret",
        "token",
        "password",
        "passwd",
        "private_key",
        "auth",
        "authorization",
        "alpaca_api_key",
        "alpaca_api_secret",
        "ibkr_password",
        "twilio_auth_token",
        "smtp_password",
        "slack_webhook_url",
        "discord_webhook_url",
    }
)

_REDACTED = "***REDACTED***"


def _redact_secrets(_: Any, __: str, event_dict: EventDict) -> EventDict:
    """Replace values of known secret keys with ``***REDACTED***``.

    Match is case-insensitive on the key. Nested dicts are walked.
    """
    return _walk_redact(event_dict)


def _walk_redact(d: EventDict) -> EventDict:
    for key, value in list(d.items()):
        lk = key.lower()
        if lk in _SECRET_KEYS:
            d[key] = _REDACTED
        elif isinstance(value, dict):
            d[key] = _walk_redact(value)
    return d


_configured = False


def configure_logging(level: str = "INFO", fmt: str = "console") -> None:
    """Configure structlog. Idempotent — calling twice is safe."""
    global _configured

    log_level = getattr(logging, level.upper(), logging.INFO)

    # Plumb stdlib logging through structlog so libs (asyncpg, redis) integrate.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
        force=True,
    )

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact_secrets,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: Processor
    if fmt == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a bound logger. Auto-configures with INFO/console defaults if needed.

    Production code should call ``configure_logging`` explicitly during startup with
    values from ``Settings.logging``.
    """
    if not _configured:
        configure_logging()
    return structlog.get_logger(name)  # type: ignore[no-any-return]


def bind_strategy(strategy_id: str) -> None:
    """Bind strategy_id into the contextvar so it appears on every subsequent log
    in this task."""
    structlog.contextvars.bind_contextvars(strategy_id=strategy_id)


def bind_request(request_id: str) -> None:
    structlog.contextvars.bind_contextvars(request_id=request_id)


def clear_context() -> None:
    structlog.contextvars.clear_contextvars()
