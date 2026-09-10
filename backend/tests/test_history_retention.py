"""Old history is pruned; rows that still steer behaviour are not."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import Settings
from app.core.time import utc_now
from app.models.enums import MediaType
from app.simple.models import (
    ActivityLog,
    AutomationJob,
    AutomationJobState,
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
from app.simple.retention import prune_history


def _candidate(search_id: str, torrent_id: str) -> ReleaseCandidate:
    return ReleaseCandidate(
        search_id=search_id,
        site_id="avistaz",
        torrent_id=torrent_id,
        title=f"Retention.{torrent_id}.1080p",
        score=0.8,
    )


@pytest.mark.asyncio
async def test_old_history_goes_and_what_still_matters_stays(session_factory) -> None:
    now = utc_now()
    old = now - timedelta(days=200)
    async with session_factory() as session:
        movie = LibraryMediaItem(
            source_item_id="retention-movie",
            media_type=MediaType.MOVIE,
            tmdb_id=7001,
            title="Retention Movie",
            state=MediaState.READY,
        )
        show = LibraryMediaItem(
            source_item_id="retention-show",
            media_type=MediaType.TV,
            tmdb_id=7002,
            title="Retention Show",
            state=MediaState.READY,
        )
        session.add_all([movie, show])
        await session.flush()

        stale_search = ReleaseSearch(
            media_id=movie.id, site_ids=["avistaz"], state=SearchState.SUCCEEDED, created_at=old
        )
        downloaded_search = ReleaseSearch(
            media_id=movie.id,
            site_ids=["avistaz"],
            state=SearchState.SUCCEEDED,
            created_at=old + timedelta(days=1),
        )
        latest_movie_search = ReleaseSearch(
            media_id=movie.id, site_ids=["avistaz"], state=SearchState.SUCCEEDED, created_at=now
        )
        # Old, but still the only search the show has had.
        only_show_search = ReleaseSearch(
            media_id=show.id, site_ids=["avistaz"], state=SearchState.FAILED, created_at=old
        )
        session.add_all([stale_search, downloaded_search, latest_movie_search, only_show_search])
        await session.flush()
        stale_candidate = _candidate(stale_search.id, "stale")
        downloaded_candidate = _candidate(downloaded_search.id, "kept")
        session.add_all(
            [stale_candidate, _candidate(stale_search.id, "stale-2"), downloaded_candidate]
        )
        await session.flush()
        session.add(
            Download(
                media_id=movie.id,
                candidate_id=downloaded_candidate.id,
                name="kept",
                state=DownloadState.COMPLETED,
            )
        )

        emptied_run = AutomationRun(
            trigger="scheduled", state=AutomationRunState.SUCCEEDED, created_at=old
        )
        blocking_run = AutomationRun(
            trigger="scheduled", state=AutomationRunState.SUCCEEDED, created_at=old
        )
        recent_run = AutomationRun(
            trigger="manual", state=AutomationRunState.SUCCEEDED, created_at=now
        )
        session.add_all([emptied_run, blocking_run, recent_run])
        await session.flush()
        superseded = AutomationJob(
            run_id=emptied_run.id,
            media_id=movie.id,
            state=AutomationJobState.FAILED,
            created_at=old,
            superseded_at=old,
            search_id=stale_search.id,
            selected_candidate_id=stale_candidate.id,
        )
        # An old failure nobody retried keeps the show out of automation.
        blocking_failure = AutomationJob(
            run_id=blocking_run.id,
            media_id=show.id,
            state=AutomationJobState.FAILED,
            error_code="QB_ADD_OUTCOME_UNKNOWN",
            created_at=old,
        )
        older_show_job = AutomationJob(
            run_id=emptied_run.id,
            media_id=show.id,
            state=AutomationJobState.SUCCEEDED,
            created_at=old - timedelta(days=1),
        )
        session.add_all([superseded, blocking_failure, older_show_job])
        await session.flush()
        latest_movie_job = AutomationJob(
            run_id=recent_run.id,
            media_id=movie.id,
            state=AutomationJobState.SUCCEEDED,
            created_at=now,
            retry_of_job_id=superseded.id,
            search_id=stale_search.id,
        )
        session.add(latest_movie_job)
        session.add_all(
            [
                ActivityLog(event="OLD", message="old", created_at=old),
                ActivityLog(event="NEW", message="new", created_at=now),
            ]
        )
        await session.commit()

        result = await prune_history(session, retention_days=90, now=now)

    assert (result.jobs, result.runs, result.searches, result.candidates, result.activity) == (
        2,
        1,
        1,
        2,
        1,
    )
    async with session_factory() as session:
        assert set(await session.scalars(select(AutomationJob.id))) == {
            blocking_failure.id,
            latest_movie_job.id,
        }
        assert set(await session.scalars(select(AutomationRun.id))) == {
            blocking_run.id,
            recent_run.id,
        }
        assert set(await session.scalars(select(ReleaseSearch.id))) == {
            downloaded_search.id,
            latest_movie_search.id,
            only_show_search.id,
        }
        assert list(await session.scalars(select(ReleaseCandidate.id))) == [
            downloaded_candidate.id
        ]
        assert list(await session.scalars(select(ActivityLog.event))) == ["NEW"]
        kept = await session.get(AutomationJob, latest_movie_job.id)
        assert kept is not None
        assert kept.retry_of_job_id is None
        assert kept.search_id is None


@pytest.mark.asyncio
async def test_recent_history_is_left_alone(session_factory) -> None:
    now = utc_now()
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="retention-recent",
            media_type=MediaType.MOVIE,
            tmdb_id=7003,
            title="Recent Movie",
            state=MediaState.READY,
        )
        session.add(media)
        await session.flush()
        older_run = AutomationRun(
            trigger="scheduled",
            state=AutomationRunState.SUCCEEDED,
            created_at=now - timedelta(days=10),
        )
        newer_run = AutomationRun(
            trigger="scheduled",
            state=AutomationRunState.SUCCEEDED,
            created_at=now - timedelta(days=1),
        )
        session.add_all([older_run, newer_run])
        await session.flush()
        session.add_all(
            [
                AutomationJob(
                    run_id=older_run.id,
                    media_id=media.id,
                    state=AutomationJobState.SUCCEEDED,
                    created_at=now - timedelta(days=10),
                ),
                AutomationJob(
                    run_id=newer_run.id,
                    media_id=media.id,
                    state=AutomationJobState.SUCCEEDED,
                    created_at=now - timedelta(days=1),
                ),
            ]
        )
        await session.commit()

        result = await prune_history(session, retention_days=90, now=now)

    assert result.total == 0


def test_retention_is_off_or_at_least_thirty_days() -> None:
    assert Settings(history_retention_days=0).history_retention_days == 0
    assert Settings(history_retention_days=30).history_retention_days == 30
    with pytest.raises(ValidationError):
        Settings(history_retention_days=7)
