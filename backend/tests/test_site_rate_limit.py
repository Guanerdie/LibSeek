from __future__ import annotations

from typing import Any

import pytest

from app.services.site_rate_limit import PostgresAdvisoryRequestGate


class FakeTransaction:
    async def __aenter__(self) -> FakeTransaction:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class FakeSession:
    def __init__(self, events: list[object]) -> None:
        self.events = events

    async def __aenter__(self) -> FakeSession:
        self.events.append("session-enter")
        return self

    async def __aexit__(self, *_: object) -> None:
        self.events.append("session-exit")

    def begin(self) -> FakeTransaction:
        return FakeTransaction()

    async def execute(self, statement: object, parameters: dict[str, Any]) -> None:
        self.events.append((str(statement), parameters["lock_id"]))


class FakeSessionFactory:
    def __init__(self, events: list[object]) -> None:
        self.events = events

    def __call__(self) -> FakeSession:
        return FakeSession(self.events)


@pytest.mark.asyncio
async def test_site_gate_holds_database_lock_through_request_and_cooldown() -> None:
    events: list[object] = []

    async def sleep(seconds: float) -> None:
        events.append(("cooldown", seconds))

    gate = PostgresAdvisoryRequestGate(
        FakeSessionFactory(events),  # type: ignore[arg-type]
        "avistaz",
        cooldown_seconds=6,
        sleep=sleep,
    )
    async with gate.limit("search"):
        events.append("request")

    assert events[0] == "session-enter"
    sql, lock_id = events[1]  # type: ignore[misc]
    assert "pg_advisory_xact_lock" in sql
    assert lock_id == gate.lock_id
    assert events[2:] == ["request", ("cooldown", 6), "session-exit"]


def test_site_gate_lock_id_is_stable_and_site_specific() -> None:
    events: list[object] = []
    factory = FakeSessionFactory(events)
    first = PostgresAdvisoryRequestGate(factory, "avistaz", cooldown_seconds=6)  # type: ignore[arg-type]
    second = PostgresAdvisoryRequestGate(factory, "avistaz", cooldown_seconds=1)  # type: ignore[arg-type]
    other = PostgresAdvisoryRequestGate(factory, "another-site", cooldown_seconds=6)  # type: ignore[arg-type]

    assert first.lock_id == second.lock_id
    assert first.lock_id != other.lock_id
