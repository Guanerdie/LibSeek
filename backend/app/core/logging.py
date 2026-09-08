"""Structured logging for the decisions automation makes.

The interesting question when a run finds nothing is never "did it crash" --
it is "why was every candidate rejected".  That answer used to exist only
inside ``AutomationJob.decision``, one row at a time, with no way to ask "what
rejected the most candidates this week".  Emitting the same decisions as JSON
lines makes them countable; ``scripts/analyze_automation_logs.py`` does the
counting.
"""

from __future__ import annotations

import logging
from typing import Any

import structlog


def configure_logging(*, level: int = logging.INFO) -> None:
    """Render every structlog event as one JSON line on stdout.

    Called from the app lifespan rather than at import time so that tests and
    one-off scripts keep structlog's readable console output.
    """

    structlog.configure(
        processors=[
            # Job-level context (run, job, media) is bound once per job and
            # merged into every event below it, so individual call sites do
            # not have to thread the ids through.
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)


def bind_job_context(**values: Any) -> None:
    """Attach ids that every event inside one automation job should carry."""

    structlog.contextvars.bind_contextvars(**values)


def clear_job_context() -> None:
    structlog.contextvars.clear_contextvars()
