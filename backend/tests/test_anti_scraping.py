"""Requests should not announce themselves as a Python script.

Optimisation 5 in ``NEXT_OPTIMIZATION_PLAN.md``.  The adapters already backed
off on 429; what they did not do was send a User-Agent at all, so httpx's
default ``python-httpx/0.28.x`` went out on every request.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.http import (
    BROWSER_USER_AGENTS,
    SafeAsyncHttpClient,
    backoff_delay,
    pick_user_agent,
)

# ---------------------------------------------------------------------------
# User-Agent
# ---------------------------------------------------------------------------


def test_every_shipped_user_agent_looks_like_a_browser() -> None:
    assert BROWSER_USER_AGENTS
    for agent in BROWSER_USER_AGENTS:
        assert agent.startswith("Mozilla/5.0")
        assert "python" not in agent.casefold()
        assert "httpx" not in agent.casefold()


def test_the_same_seed_always_gets_the_same_identity() -> None:
    """A session whose User-Agent changes per request is the bigger tell."""

    first = pick_user_agent("https://example.invalid")
    second = pick_user_agent("https://example.invalid")

    assert first == second
    assert first in BROWSER_USER_AGENTS


def test_different_sites_can_get_different_identities() -> None:
    agents = {pick_user_agent(f"https://site-{index}.invalid") for index in range(40)}

    # Not a guarantee for any given pair, but 40 seeds must not all collide.
    assert len(agents) > 1


def test_an_unseeded_pick_is_still_a_real_browser_string() -> None:
    assert pick_user_agent() in BROWSER_USER_AGENTS


@pytest.mark.asyncio
async def test_the_safe_client_sends_the_user_agent_it_was_given() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("user-agent", ""))
        return httpx.Response(200, json={})

    client = SafeAsyncHttpClient(
        base_url="https://example.com",
        allowed_hosts=("example.com",),
        connect_timeout=1.0,
        read_timeout=1.0,
        max_response_bytes=1024,
        transport=httpx.MockTransport(handler),
        user_agent="Mozilla/5.0 (Test) TestBrowser/1.0",
    )
    try:
        await client.request("GET", "/thing")
    finally:
        await client.aclose()

    assert seen == ["Mozilla/5.0 (Test) TestBrowser/1.0"]


@pytest.mark.asyncio
async def test_without_one_the_client_keeps_httpx_defaults() -> None:
    """Passing nothing must not crash or send an empty header."""

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("user-agent", ""))
        return httpx.Response(200, json={})

    client = SafeAsyncHttpClient(
        base_url="https://example.com",
        allowed_hosts=("example.com",),
        connect_timeout=1.0,
        read_timeout=1.0,
        max_response_bytes=1024,
        transport=httpx.MockTransport(handler),
    )
    try:
        await client.request("GET", "/thing")
    finally:
        await client.aclose()

    assert seen and seen[0]


# ---------------------------------------------------------------------------
# Backoff
# ---------------------------------------------------------------------------


def test_backoff_grows_exponentially() -> None:
    delays = [backoff_delay(attempt, random_source=lambda: 0.0) for attempt in range(4)]

    assert delays == [2.0, 4.0, 8.0, 16.0]


def test_retry_after_raises_the_floor_but_never_lowers_it() -> None:
    # Site asked for longer than our own backoff: honour the site.
    assert backoff_delay(0, retry_after=30.0, random_source=lambda: 0.0) == 30.0
    # Site asked for less: keep backing off anyway.
    assert backoff_delay(3, retry_after=1.0, random_source=lambda: 0.0) == 16.0


def test_jitter_is_added_on_top_and_stays_bounded() -> None:
    """Identical delays make every client retry in lockstep after a blip."""

    lowest = backoff_delay(1, random_source=lambda: 0.0)
    highest = backoff_delay(1, random_source=lambda: 0.999)

    assert lowest == 4.0
    assert 4.999 <= highest < 5.0


def test_a_negative_attempt_does_not_produce_a_shrinking_delay() -> None:
    assert backoff_delay(-3, random_source=lambda: 0.0) == 2.0
