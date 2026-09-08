"""The PT/TMDB request pace must survive adapters being rebuilt per job.

Optimisation 1 in ``NEXT_OPTIMIZATION_PLAN.md`` asks for the request rate to
stay inside the site's limit.  The plan assumed the risk came from an unbounded
``asyncio.gather``; the runner has always executed jobs one at a time, so the
real leak was elsewhere: every search builds a fresh adapter, and a per-adapter
limiter forgets when the previous request went out.
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.http import SerializedRateLimiter, shared_rate_limiter


class FakeClock:
    """A monotonic clock that only advances when the limiter sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


async def test_a_fresh_limiter_per_adapter_does_not_throttle_anything() -> None:
    """The old behaviour, pinned so the regression is unmistakable."""

    clock = FakeClock()
    for _ in range(5):
        # One limiter per adapter instance, exactly as building an adapter per
        # search used to produce.
        limiter = SerializedRateLimiter(6.0, monotonic=clock.monotonic, sleep=clock.sleep)
        await limiter.acquire("search")

    assert clock.sleeps == []


async def test_one_shared_limiter_paces_every_rebuilt_adapter() -> None:
    clock = FakeClock()
    limiter = SerializedRateLimiter(6.0, monotonic=clock.monotonic, sleep=clock.sleep)

    for _ in range(5):
        await limiter.acquire("search")

    # First request goes straight out; the other four each wait a full interval.
    assert clock.sleeps == [6.0, 6.0, 6.0, 6.0]


async def test_the_registry_hands_back_the_same_limiter_for_one_site() -> None:
    first = shared_rate_limiter("pt:avistaz", 6.0)
    second = shared_rate_limiter("pt:avistaz", 6.0)

    assert first is second


async def test_different_sites_are_paced_independently() -> None:
    avistaz = shared_rate_limiter("pt:avistaz", 6.0)
    tmdb = shared_rate_limiter("tmdb", 0.25)

    assert avistaz is not tmdb
    assert tmdb.min_interval_seconds == 0.25


async def test_changing_the_configured_interval_replaces_the_limiter() -> None:
    """Otherwise a settings change would silently keep the old pace forever."""

    original = shared_rate_limiter("pt:avistaz", 6.0)
    slower = shared_rate_limiter("pt:avistaz", 12.0)

    assert original is not slower
    assert slower.min_interval_seconds == 12.0


def test_a_limiter_requested_outside_a_loop_is_not_cached() -> None:
    """``asyncio.Lock`` binds to a loop, so an unowned limiter must not leak."""

    first = shared_rate_limiter("pt:avistaz", 6.0)
    second = shared_rate_limiter("pt:avistaz", 6.0)

    assert first is not second


async def test_limiters_do_not_leak_between_event_loops() -> None:
    """A limiter reused across loops would raise "bound to a different loop"."""

    outer = shared_rate_limiter("pt:avistaz", 6.0)

    def _in_a_new_loop() -> SerializedRateLimiter:
        return asyncio.run(_fetch())

    async def _fetch() -> SerializedRateLimiter:
        limiter = shared_rate_limiter("pt:avistaz", 6.0)
        # Awaiting is what binds the lock to the running loop.
        await limiter.acquire("search")
        return limiter

    inner = await asyncio.to_thread(_in_a_new_loop)

    assert outer is not inner


@pytest.mark.asyncio
async def test_the_runner_still_executes_jobs_one_at_a_time(session_factory) -> None:
    """Guard the invariant optimisation 1 was worried about losing.

    ``_execute_job`` shares a single ``AsyncSession``, which is not safe to use
    concurrently.  If someone ever wraps the loop in ``asyncio.gather`` this
    test is the tripwire.
    """

    import inspect

    from app.simple import automation

    source = inspect.getsource(automation.run_automation)
    assert "gather" not in source
    assert "TaskGroup" not in source
