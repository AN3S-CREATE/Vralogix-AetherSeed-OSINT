"""Structured logging with correlation IDs.

Uses :mod:`structlog`. Every log line can be enriched with correlation context
(``run_id``, ``seed_id``, ``page_id``, ``worker_id``) bound to a
:class:`contextvars.ContextVar`, so per-item tracing works across async tasks
without threading identifiers through every call.

Call :func:`configure_logging` once at process start (CLI/API entrypoints do
this). Use :func:`get_logger` everywhere else.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars, unbind_contextvars

_configured = False


def configure_logging(*, level: str = "INFO", json_output: bool = False) -> None:
    """Configure structlog + stdlib logging.

    Idempotent: safe to call more than once (only the first call takes effect).

    Parameters
    ----------
    level:
        Root log level name.
    json_output:
        When ``True`` render newline-delimited JSON (production/ingest). When
        ``False`` render human-friendly colourised output (development).
    """
    global _configured
    if _configured:
        return

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=getattr(logging, level.upper(), logging.INFO),
    )

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound logger, configuring logging with defaults if needed."""
    if not _configured:
        configure_logging()
    return structlog.get_logger(name)  # type: ignore[no-any-return]


@contextmanager
def log_context(**kwargs: Any) -> Iterator[None]:
    """Bind correlation identifiers for the duration of a block.

    Examples
    --------
    >>> with log_context(run_id="r1", seed_id="s1"):
    ...     get_logger().info("fetching")  # doctest: +SKIP
    """
    tokens = bind_contextvars(**kwargs)
    try:
        yield
    finally:
        unbind_contextvars(*tokens.keys())


def reset_log_context() -> None:
    """Clear all bound correlation context (e.g. between runs in a worker)."""
    clear_contextvars()
