"""A recent identical search stands in for the next one.

Optimisation 2 in ``NEXT_OPTIMIZATION_PLAN.md``: a manual search followed
minutes later by an automation cycle used to ask the PT site the same question
twice, spending quota to learn the same thing.
"""

from __future__ import annotations

from datetime import UTC, timedelta

import pytest

from app.core.time import utc_now
from app.errors import AppError
from app.models.enums import MediaType
from app.simple.models import (
    LibraryMediaItem,
    MediaState,
    ReleaseCandidate,
    ReleaseSearch,
    SearchState,
)
from app.simple.service import SEARCH_CACHE_TTL, get_or_create_search


async def _make_media(session, *, tmdb_id: int, state: MediaState = MediaState.READY):
    media = LibraryMediaItem(
        source_item_id=f"cache-{tmdb_id}",
        media_type=MediaType.MOVIE,
        tmdb_id=tmdb_id,
        title=f"Cache {tmdb_id}",
        year=2026,
        state=state,
    )
    session.add(media)
    await session.commit()
    return media


async def _finish(session, search: ReleaseSearch, *, candidate_count: int = 1) -> None:
    """Mark a search successful the way ``complete_search`` would."""

    for index in range(candidate_count):
        session.add(
            ReleaseCandidate(
                search_id=search.id,
                site_id="avistaz",
                torrent_id=f"t{index}",
                title=f"Cached.Movie.2026.1080p.{index}",
                size_bytes=1_000_000,
                seeders=10,
                score=0.9,
            )
        )
    search.state = SearchState.SUCCEEDED
    search.finished_at = utc_now()
    await session.commit()


def test_the_cache_key_ignores_the_order_sites_were_listed_in() -> None:
    assert ReleaseSearch.make_cache_key("m1", ["avistaz", "other"]) == (
        ReleaseSearch.make_cache_key("m1", ["other", "avistaz"])
    )


def test_different_media_never_share_a_cache_entry() -> None:
    assert ReleaseSearch.make_cache_key("m1", ["avistaz"]) != (
        ReleaseSearch.make_cache_key("m2", ["avistaz"])
    )


@pytest.mark.asyncio
async def test_the_first_search_is_created_and_must_run(session_factory) -> None:
    async with session_factory() as session:
        media = await _make_media(session, tmdb_id=920)

        search, created = await get_or_create_search(
            session, media_id=media.id, site_ids=["avistaz"]
        )

        assert created is True
        assert search.cache_key is not None
        assert search.cache_expires_at is not None
        assert media.state == MediaState.SEARCHING


@pytest.mark.asyncio
async def test_an_identical_search_within_the_ttl_reuses_the_result(session_factory) -> None:
    async with session_factory() as session:
        media = await _make_media(session, tmdb_id=921)
        first, _ = await get_or_create_search(session, media_id=media.id, site_ids=["avistaz"])
        await _finish(session, first)

        second, created = await get_or_create_search(
            session, media_id=media.id, site_ids=["avistaz"]
        )

        assert created is False
        assert second.id == first.id


@pytest.mark.asyncio
async def test_force_bypasses_a_live_cache_entry(session_factory) -> None:
    async with session_factory() as session:
        media = await _make_media(session, tmdb_id=922)
        first, _ = await get_or_create_search(session, media_id=media.id, site_ids=["avistaz"])
        await _finish(session, first)

        second, created = await get_or_create_search(
            session, media_id=media.id, site_ids=["avistaz"], force=True
        )

        assert created is True
        assert second.id != first.id


@pytest.mark.asyncio
async def test_an_expired_entry_triggers_a_real_search(session_factory) -> None:
    async with session_factory() as session:
        media = await _make_media(session, tmdb_id=923)
        first, _ = await get_or_create_search(session, media_id=media.id, site_ids=["avistaz"])
        await _finish(session, first)
        first.cache_expires_at = utc_now() - timedelta(seconds=1)
        await session.commit()

        second, created = await get_or_create_search(
            session, media_id=media.id, site_ids=["avistaz"]
        )

        assert created is True
        assert second.id != first.id


@pytest.mark.asyncio
async def test_a_search_that_never_finished_is_not_served_as_a_result(session_factory) -> None:
    """Otherwise a still-running search would look like an instant empty one."""

    async with session_factory() as session:
        media = await _make_media(session, tmdb_id=924)
        first, _ = await get_or_create_search(session, media_id=media.id, site_ids=["avistaz"])
        assert first.state == SearchState.PENDING

        second, created = await get_or_create_search(
            session, media_id=media.id, site_ids=["avistaz"]
        )

        assert created is True
        assert second.id != first.id


@pytest.mark.asyncio
async def test_a_failed_search_is_not_served_as_a_result(session_factory) -> None:
    async with session_factory() as session:
        media = await _make_media(session, tmdb_id=925)
        first, _ = await get_or_create_search(session, media_id=media.id, site_ids=["avistaz"])
        first.state = SearchState.FAILED
        first.finished_at = utc_now()
        await session.commit()

        _, created = await get_or_create_search(session, media_id=media.id, site_ids=["avistaz"])

        assert created is True


@pytest.mark.asyncio
async def test_an_empty_result_is_still_worth_caching(session_factory) -> None:
    """"Nothing on the site right now" is an answer, and repeating it costs quota."""

    async with session_factory() as session:
        media = await _make_media(session, tmdb_id=926)
        first, _ = await get_or_create_search(session, media_id=media.id, site_ids=["avistaz"])
        await _finish(session, first, candidate_count=0)

        second, created = await get_or_create_search(
            session, media_id=media.id, site_ids=["avistaz"]
        )

        assert created is False
        assert second.id == first.id


@pytest.mark.asyncio
async def test_a_different_site_set_is_a_different_search(session_factory) -> None:
    async with session_factory() as session:
        media = await _make_media(session, tmdb_id=927)
        first, _ = await get_or_create_search(session, media_id=media.id, site_ids=["avistaz"])
        await _finish(session, first)

        _, created = await get_or_create_search(
            session, media_id=media.id, site_ids=["avistaz", "other"]
        )

        assert created is True


@pytest.mark.asyncio
async def test_a_downloading_media_still_refuses_to_search(session_factory) -> None:
    """The cache must not become a way around the existing guard."""

    async with session_factory() as session:
        media = await _make_media(session, tmdb_id=928, state=MediaState.DOWNLOADING)

        with pytest.raises(AppError) as excinfo:
            await get_or_create_search(session, media_id=media.id, site_ids=["avistaz"])

        assert excinfo.value.error_code == "MEDIA_ALREADY_DOWNLOADING"


@pytest.mark.asyncio
async def test_the_ttl_is_five_minutes(session_factory) -> None:
    async with session_factory() as session:
        media = await _make_media(session, tmdb_id=929)
        before = utc_now()

        search, _ = await get_or_create_search(session, media_id=media.id, site_ids=["avistaz"])

        assert search.cache_expires_at is not None
        assert SEARCH_CACHE_TTL == timedelta(minutes=5)
        # SQLite hands timestamps back without a tzinfo even for
        # ``DateTime(timezone=True)``; the stored value is UTC either way.
        expires_at = search.cache_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        # Allow a second of slack for the commit round-trip.
        assert expires_at - before >= SEARCH_CACHE_TTL - timedelta(seconds=1)
