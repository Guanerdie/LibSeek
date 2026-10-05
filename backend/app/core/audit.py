"""Who is acting, and whether a person or the system decided to.

The activity log used to say what happened and to which title, but not who did
it or why.  Threading an actor through every service function would touch most
of the code base, so the answer travels in a context variable instead: the
request's authenticated user sets it, background loops set their own, and
``ActivityLog`` stamps each new row from whatever is current.  A task started
from a request copies the context, so a search or sync a user kicked off is
still attributed to them when it finishes in the background.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Literal

Trigger = Literal["MANUAL", "AUTO"]

#: Actors that are not people.  The prefix keeps them from ever colliding with
#: a username.
SYSTEM = "system"
SCHEDULER = "system:scheduler"
CLEANUP = "system:cleanup"


@dataclass(frozen=True)
class AuditContext:
    actor: str = SYSTEM
    trigger: Trigger = "AUTO"
    # Why an automatic action happened.  Manual actions need none: a person
    # chose to do it.
    reason: str | None = None
    # Merged into every row's details, e.g. the automation run an action
    # belongs to.
    details: dict[str, object] = field(default_factory=dict)


_context: ContextVar[AuditContext | None] = ContextVar("audit_context", default=None)


def current() -> AuditContext:
    return _context.get() or AuditContext()


def is_person(actor: str) -> bool:
    return actor != SYSTEM and not actor.startswith(f"{SYSTEM}:")


def bind(
    *,
    actor: str | None = None,
    trigger: Trigger | None = None,
    reason: str | None = None,
    details: dict[str, object] | None = None,
) -> None:
    """Set the context for the rest of the current task."""

    _context.set(_merged(actor=actor, trigger=trigger, reason=reason, details=details))


@contextmanager
def scope(
    *,
    actor: str | None = None,
    trigger: Trigger | None = None,
    reason: str | None = None,
    details: dict[str, object] | None = None,
) -> Iterator[None]:
    """Override parts of the context for one block."""

    token = _context.set(
        _merged(actor=actor, trigger=trigger, reason=reason, details=details)
    )
    try:
        yield
    finally:
        _context.reset(token)


def _merged(
    *,
    actor: str | None,
    trigger: Trigger | None,
    reason: str | None,
    details: dict[str, object] | None,
) -> AuditContext:
    base = current()
    return replace(
        base,
        actor=actor if actor is not None else base.actor,
        trigger=trigger if trigger is not None else base.trigger,
        reason=reason if reason is not None else base.reason,
        details={**base.details, **(details or {})},
    )
