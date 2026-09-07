"""Regression tests for the two review findings that survived the 0.9 rebuild.

* A job that failed once used to keep its media out of automation forever
  (review 2.1).
* A torrent deleted inside qBittorrent used to leave the download -- and the
  media -- stuck in ``DOWNLOADING`` with no way back (review 2.6).
"""

from __future__ import annotations

from datetime import timedelta
from typing import cast

import pytest
from sqlalchemy import select

from app.adapters.downloaders.qbittorrent import QbittorrentAdapter
from app.adapters.pt_sites.avistaz import AvistaZMockAdapter
from app.core.time import utc_now
from app.errors import AppError
from app.models.enums import MediaType
from app.schemas.adapters import TorrentCandidate, TorrentSearchRequest
from app.schemas.qbittorrent import QbTorrent
from app.simple.automation import (
    _release_recoverable_failed_jobs,
    run_dry_run,
    update_policy,
)
from app.simple.integrations import sync_download_statuses
from app.simple.models import (
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
)
from app.simple.schemas import AutomationPolicyUpdate


class EmptyQb:
    """A qBittorrent that knows about no torrents at all."""

    async def authenticate(self) -> None:
        return None

    async def list_torrents(self) -> list[QbTorrent]:
        return []


async def _make_media(session, *, tmdb_id: int, state: MediaState) -> LibraryMediaItem:
    media = LibraryMediaItem(
        source_item_id=f"review-fix-{tmdb_id}",
        media_type=MediaType.MOVIE,
        tmdb_id=tmdb_id,
        title=f"Review Fix {tmdb_id}",
        year=2026,
        state=state,
    )
    session.add(media)
    await session.flush()
    return media


async def _make_failed_job(
    session,
    media: LibraryMediaItem,
    *,
    error_code: str | None,
    age: timedelta,
) -> AutomationJob:
    job = AutomationJob(
        run_id="review-fix-run",
        media_id=media.id,
        state=AutomationJobState.FAILED,
        error_code=error_code,
        error_message="失败",
        finished_at=utc_now() - age,
    )
    session.add(job)
    await session.commit()
    return job


async def _make_download(
    session,
    media: LibraryMediaItem,
    *,
    info_hash: str,
    state: DownloadState,
    submitted_age: timedelta | None,
    progress: float = 0.5,
) -> Download:
    search = ReleaseSearch(media_id=media.id, site_ids=["avistaz"])
    session.add(search)
    await session.flush()
    candidate = ReleaseCandidate(
        search_id=search.id,
        site_id="avistaz",
        torrent_id=info_hash[:8],
        title="Vanished.Movie.2026.1080p.WEB-DL",
        size_bytes=2_000_000,
        seeders=5,
        score=0.9,
    )
    session.add(candidate)
    await session.flush()
    download = Download(
        media_id=media.id,
        candidate_id=candidate.id,
        info_hash=info_hash,
        name=candidate.title,
        state=state,
        progress=progress,
        submitted_at=None if submitted_age is None else utc_now() - submitted_age,
    )
    session.add(download)
    await session.commit()
    return download


# --------------------------------------------------------------------------
# 2.1 -- failed jobs must not retire a media item permanently
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_transient_failure_is_released_after_the_cooling_off_period(
    session_factory,
) -> None:
    async with session_factory() as session:
        media = await _make_media(session, tmdb_id=901, state=MediaState.NEEDS_ATTENTION)
        job = await _make_failed_job(
            session, media, error_code="PT_TEMPORARY_FAILURE", age=timedelta(hours=7)
        )

        released = await _release_recoverable_failed_jobs(session)

        assert released == 1
        # The original run's outcome is untouched; only the queue entry closes.
        assert job.state == AutomationJobState.FAILED
        assert job.superseded_at is not None


@pytest.mark.asyncio
async def test_a_failure_needing_a_human_is_never_released(session_factory) -> None:
    async with session_factory() as session:
        media = await _make_media(session, tmdb_id=902, state=MediaState.NEEDS_ATTENTION)
        job = await _make_failed_job(
            session, media, error_code="MEDIA_IDENTITY_REQUIRED", age=timedelta(days=30)
        )

        released = await _release_recoverable_failed_jobs(session)

        assert released == 0
        assert job.superseded_at is None


@pytest.mark.asyncio
async def test_a_recent_failure_is_left_alone_until_the_delay_elapses(
    session_factory,
) -> None:
    async with session_factory() as session:
        media = await _make_media(session, tmdb_id=903, state=MediaState.NEEDS_ATTENTION)
        job = await _make_failed_job(
            session, media, error_code="PT_TEMPORARY_FAILURE", age=timedelta(hours=1)
        )

        released = await _release_recoverable_failed_jobs(session)

        assert released == 0
        assert job.superseded_at is None


@pytest.mark.asyncio
async def test_an_unexpected_crash_is_recorded_by_code_and_released(
    session_factory,
) -> None:
    """A non-AppError failure gets a code of its own so it can still be freed."""

    async with session_factory() as session:
        media = await _make_media(session, tmdb_id=904, state=MediaState.NEEDS_ATTENTION)
        job = await _make_failed_job(
            session, media, error_code="UNEXPECTED_ERROR", age=timedelta(hours=7)
        )

        assert await _release_recoverable_failed_jobs(session) == 1
        assert job.superseded_at is not None


@pytest.mark.asyncio
async def test_a_released_media_is_picked_up_by_the_next_automation_run(
    session_factory,
) -> None:
    async with session_factory() as session:
        media = await _make_media(session, tmdb_id=905, state=MediaState.NEEDS_ATTENTION)
        stale = await _make_failed_job(
            session, media, error_code="PT_TEMPORARY_FAILURE", age=timedelta(hours=7)
        )
        await update_policy(session, AutomationPolicyUpdate(enabled=True))

        _, created, _, _ = await run_dry_run(
            session, adapter_factory=lambda _site_id: AvistaZMockAdapter()
        )

        assert created == 1
        assert stale.superseded_at is not None
        fresh = await session.scalar(
            select(AutomationJob).where(
                AutomationJob.media_id == media.id,
                AutomationJob.id != stale.id,
            )
        )
        assert fresh is not None


@pytest.mark.asyncio
async def test_a_job_needing_a_human_still_blocks_the_next_run(session_factory) -> None:
    async with session_factory() as session:
        media = await _make_media(session, tmdb_id=906, state=MediaState.NEEDS_ATTENTION)
        blocked = await _make_failed_job(
            session, media, error_code="TMDB_SELECTION_REQUIRED", age=timedelta(days=3)
        )
        await update_policy(session, AutomationPolicyUpdate(enabled=True))

        _, created, _, _ = await run_dry_run(
            session, adapter_factory=lambda _site_id: AvistaZMockAdapter()
        )

        assert created == 0
        assert blocked.superseded_at is None


@pytest.mark.asyncio
async def test_a_failing_job_records_the_error_code_that_caused_it(
    session_factory,
) -> None:
    class ConfigurationErrorAdapter(AvistaZMockAdapter):
        async def search(self, request: TorrentSearchRequest) -> list[TorrentCandidate]:
            del request
            raise AppError("PT_CONFIGURATION_ERROR", "PT 配置错误", retryable=False)

    async with session_factory() as session:
        await _make_media(session, tmdb_id=907, state=MediaState.READY)
        await session.commit()
        await update_policy(session, AutomationPolicyUpdate(enabled=True))

        await run_dry_run(session, adapter_factory=lambda _site_id: ConfigurationErrorAdapter())

        job = await session.scalar(select(AutomationJob))
        assert job is not None
        assert job.state == AutomationJobState.FAILED
        assert job.error_code == "PT_CONFIGURATION_ERROR"


# --------------------------------------------------------------------------
# 2.6 -- a torrent deleted in qBittorrent must not strand the media
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_download_missing_from_qb_releases_the_media(session_factory) -> None:
    async with session_factory() as session:
        media = await _make_media(session, tmdb_id=910, state=MediaState.DOWNLOADING)
        download = await _make_download(
            session,
            media,
            info_hash="b" * 40,
            state=DownloadState.DOWNLOADING,
            submitted_age=timedelta(minutes=30),
        )

        updated = await sync_download_statuses(session, cast(QbittorrentAdapter, EmptyQb()))

        assert updated == 1
        assert download.state == DownloadState.ERROR
        assert download.error_message == "下载器中已不存在该任务"
        assert media.state == MediaState.NEEDS_ATTENTION
        assert media.attention_reason == "qBittorrent 中已不存在该下载任务"


@pytest.mark.asyncio
async def test_a_just_submitted_download_gets_a_grace_period(session_factory) -> None:
    async with session_factory() as session:
        media = await _make_media(session, tmdb_id=911, state=MediaState.DOWNLOADING)
        download = await _make_download(
            session,
            media,
            info_hash="c" * 40,
            state=DownloadState.SUBMITTING,
            submitted_age=timedelta(minutes=1),
            progress=0,
        )

        updated = await sync_download_statuses(session, cast(QbittorrentAdapter, EmptyQb()))

        assert updated == 0
        assert download.state == DownloadState.SUBMITTING
        assert media.state == MediaState.DOWNLOADING


@pytest.mark.asyncio
async def test_a_vanished_download_also_frees_its_failed_automation_job(
    session_factory,
) -> None:
    """Releasing the media is not enough -- a live FAILED job also blocks it."""

    async with session_factory() as session:
        media = await _make_media(session, tmdb_id=912, state=MediaState.DOWNLOADING)
        download = await _make_download(
            session,
            media,
            info_hash="d" * 40,
            state=DownloadState.OUTCOME_UNKNOWN,
            submitted_age=timedelta(hours=2),
        )
        run = AutomationRun(trigger="manual", state=AutomationRunState.FAILED, failed_count=1)
        session.add(run)
        await session.flush()
        job = AutomationJob(
            run_id=run.id,
            media_id=media.id,
            download_id=download.id,
            state=AutomationJobState.FAILED,
            error_code="QB_ADD_OUTCOME_UNKNOWN",
        )
        session.add(job)
        await session.commit()

        await sync_download_statuses(session, cast(QbittorrentAdapter, EmptyQb()))

        assert download.state == DownloadState.ERROR
        assert media.state == MediaState.NEEDS_ATTENTION
        assert job.state == AutomationJobState.FAILED
        assert job.superseded_at is not None


@pytest.mark.asyncio
async def test_a_completed_download_removed_from_qb_is_not_an_error(
    session_factory,
) -> None:
    async with session_factory() as session:
        media = await _make_media(session, tmdb_id=913, state=MediaState.DOWNLOADING)
        download = await _make_download(
            session,
            media,
            info_hash="e" * 40,
            state=DownloadState.SEEDING,
            submitted_age=timedelta(days=2),
            progress=1,
        )

        updated = await sync_download_statuses(session, cast(QbittorrentAdapter, EmptyQb()))

        assert updated == 1
        assert download.state == DownloadState.COMPLETED
        assert download.error_message is None
        assert media.state == MediaState.COMPLETE
