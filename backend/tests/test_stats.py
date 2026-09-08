"""Aggregates for the monitoring page.

Optimisation 7 in ``NEXT_OPTIMIZATION_PLAN.md``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.time import utc_now
from app.models.enums import MediaType
from app.simple.models import (
    AutomationRun,
    AutomationRunState,
    Download,
    DownloadState,
    LibraryMediaItem,
    MediaState,
    ReleaseCandidate,
    ReleaseSearch,
    SearchState,
)
from app.simple.stats import collect_stats


async def _media(session, *, tmdb_id: int, state: MediaState) -> LibraryMediaItem:
    item = LibraryMediaItem(
        source_item_id=f"stats-{tmdb_id}",
        media_type=MediaType.MOVIE,
        tmdb_id=tmdb_id,
        title=f"Stats {tmdb_id}",
        year=2026,
        state=state,
    )
    session.add(item)
    await session.flush()
    return item


async def _search(
    session,
    media: LibraryMediaItem,
    *,
    state: SearchState,
    age: timedelta = timedelta(0),
    duration: timedelta | None = None,
    site_ids: list[str] | None = None,
) -> ReleaseSearch:
    created = utc_now() - age
    search = ReleaseSearch(
        media_id=media.id,
        site_ids=site_ids or ["avistaz"],
        state=state,
        created_at=created,
        finished_at=None if duration is None else created + duration,
    )
    session.add(search)
    await session.flush()
    return search


@pytest.mark.asyncio
async def test_the_trend_always_covers_seven_days(session_factory) -> None:
    async with session_factory() as session:
        await session.commit()

        report = await collect_stats(session)

        assert report["window_days"] == 7
        trend = report["search_trend"]
        assert len(trend) == 7
        assert [day["total"] for day in trend] == [0] * 7
        # A day with no searches has no rate, rather than a rate of zero.
        assert all(day["success_rate"] is None for day in trend)


@pytest.mark.asyncio
async def test_a_days_success_rate_counts_only_finished_searches(session_factory) -> None:
    async with session_factory() as session:
        media = await _media(session, tmdb_id=800, state=MediaState.READY)
        await _search(session, media, state=SearchState.SUCCEEDED)
        await _search(session, media, state=SearchState.SUCCEEDED)
        await _search(session, media, state=SearchState.FAILED)
        # Still running: not evidence either way yet.
        await _search(session, media, state=SearchState.RUNNING)
        await session.commit()

        report = await collect_stats(session)

        today = report["search_trend"][-1]
        assert today["total"] == 3
        assert today["succeeded"] == 2
        assert today["success_rate"] == pytest.approx(2 / 3)


@pytest.mark.asyncio
async def test_searches_older_than_the_window_are_excluded(session_factory) -> None:
    async with session_factory() as session:
        media = await _media(session, tmdb_id=801, state=MediaState.READY)
        await _search(session, media, state=SearchState.SUCCEEDED, age=timedelta(days=30))
        await session.commit()

        report = await collect_stats(session)

        assert sum(day["total"] for day in report["search_trend"]) == 0


@pytest.mark.asyncio
async def test_site_latency_is_measured_from_the_search_span(session_factory) -> None:
    async with session_factory() as session:
        media = await _media(session, tmdb_id=802, state=MediaState.READY)
        await _search(
            session, media, state=SearchState.SUCCEEDED, duration=timedelta(seconds=4)
        )
        await _search(
            session, media, state=SearchState.SUCCEEDED, duration=timedelta(seconds=10)
        )
        await session.commit()

        report = await collect_stats(session)

        assert report["site_latency"] == [
            {
                "site_id": "avistaz",
                "searches": 2,
                "average_seconds": 7.0,
                "slowest_seconds": 10.0,
            }
        ]


@pytest.mark.asyncio
async def test_a_search_across_two_sites_counts_for_both(session_factory) -> None:
    async with session_factory() as session:
        media = await _media(session, tmdb_id=803, state=MediaState.READY)
        await _search(
            session,
            media,
            state=SearchState.SUCCEEDED,
            duration=timedelta(seconds=6),
            site_ids=["avistaz", "other"],
        )
        await session.commit()

        report = await collect_stats(session)

        assert [row["site_id"] for row in report["site_latency"]] == ["avistaz", "other"]


@pytest.mark.asyncio
async def test_an_unfinished_search_contributes_no_latency(session_factory) -> None:
    async with session_factory() as session:
        media = await _media(session, tmdb_id=804, state=MediaState.READY)
        await _search(session, media, state=SearchState.RUNNING)
        await session.commit()

        report = await collect_stats(session)

        assert report["site_latency"] == []


@pytest.mark.asyncio
async def test_run_history_reports_duration_and_counts(session_factory) -> None:
    async with session_factory() as session:
        started = utc_now() - timedelta(seconds=30)
        session.add(
            AutomationRun(
                trigger="scheduled",
                state=AutomationRunState.SUCCEEDED,
                created_count=3,
                succeeded_count=2,
                failed_count=1,
                started_at=started,
                finished_at=started + timedelta(seconds=12),
            )
        )
        await session.commit()

        report = await collect_stats(session)

        assert len(report["recent_runs"]) == 1
        run = report["recent_runs"][0]
        assert run["trigger"] == "scheduled"
        assert run["state"] == "SUCCEEDED"
        assert (run["created_count"], run["succeeded_count"], run["failed_count"]) == (3, 2, 1)
        assert run["duration_seconds"] == 12.0


@pytest.mark.asyncio
async def test_a_run_that_never_finished_has_no_duration(session_factory) -> None:
    async with session_factory() as session:
        session.add(
            AutomationRun(
                trigger="manual",
                state=AutomationRunState.RUNNING,
                started_at=utc_now(),
            )
        )
        await session.commit()

        report = await collect_stats(session)

        assert report["recent_runs"][0]["duration_seconds"] is None


@pytest.mark.asyncio
async def test_coverage_counts_downloading_as_already_handled(session_factory) -> None:
    async with session_factory() as session:
        await _media(session, tmdb_id=810, state=MediaState.COMPLETE)
        await _media(session, tmdb_id=811, state=MediaState.DOWNLOADING)
        await _media(session, tmdb_id=812, state=MediaState.READY)
        await _media(session, tmdb_id=813, state=MediaState.NEEDS_ATTENTION)
        await session.commit()

        coverage = (await collect_stats(session))["library_coverage"]

        assert coverage["total"] == 4
        assert coverage["covered"] == 2
        assert coverage["coverage_rate"] == 0.5
        assert coverage["by_state"]["NEEDS_ATTENTION"] == 1


@pytest.mark.asyncio
async def test_an_empty_library_has_no_coverage_rate(session_factory) -> None:
    async with session_factory() as session:
        await session.commit()

        coverage = (await collect_stats(session))["library_coverage"]

        assert coverage["total"] == 0
        assert coverage["coverage_rate"] is None


@pytest.mark.asyncio
async def test_download_health_surfaces_the_errored_count(session_factory) -> None:
    async with session_factory() as session:
        media = await _media(session, tmdb_id=820, state=MediaState.DOWNLOADING)
        search = await _search(session, media, state=SearchState.SUCCEEDED)
        candidate = ReleaseCandidate(
            search_id=search.id,
            site_id="avistaz",
            torrent_id="stats-1",
            title="Stats.Movie.2026.1080p",
            size_bytes=1_000,
            seeders=1,
            score=0.9,
        )
        session.add(candidate)
        await session.flush()
        session.add(
            Download(
                media_id=media.id,
                candidate_id=candidate.id,
                name=candidate.title,
                state=DownloadState.ERROR,
            )
        )
        await session.commit()

        health = (await collect_stats(session))["download_health"]

        assert health["total"] == 1
        assert health["errored"] == 1
        assert health["by_state"]["ERROR"] == 1
