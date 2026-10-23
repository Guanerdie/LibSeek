from __future__ import annotations

from datetime import timedelta
from typing import cast

import pytest
from sqlalchemy import select

from app.adapters.downloaders.qbittorrent import QbAddResult, QbittorrentAdapter
from app.core import audit
from app.core.time import utc_now
from app.errors import AppError
from app.models.enums import MediaType
from app.schemas.qbittorrent import QbTorrent
from app.simple.integrations import retry_download, submit_download, sync_download_statuses
from app.simple.models import (
    ActivityLog,
    Download,
    DownloadCleanupState,
    DownloadState,
    MediaState,
    ReleaseCandidate,
)
from app.simple.service import queue_download
from tests.test_simplified_daily_flow import (
    CapturingQb,
    FakeTorrentSource,
    StatusQb,
    add_candidate,
    torrent_fixture,
)


async def _finished_download(session, *, confirmed=False, cleanup=DownloadCleanupState.NONE):
    payload, info_hash = torrent_fixture()
    media, candidate = await add_candidate(
        session, source_item_id="reconcile-finished", info_hash=info_hash
    )
    media.state = MediaState.COMPLETE
    if confirmed:
        media.library_confirmed_at = utc_now() - timedelta(days=1)
    download = Download(
        media_id=media.id,
        candidate_id=candidate.id,
        info_hash=info_hash,
        name=candidate.title,
        state=DownloadState.COMPLETED,
        progress=1,
        submitted_at=utc_now() - timedelta(days=3),
        completed_at=utc_now() - timedelta(days=2),
        cleanup_state=cleanup,
        cleanup_deleted_at=utc_now() - timedelta(days=1)
        if cleanup == DownloadCleanupState.DELETED else None,
    )
    session.add(download)
    await session.commit()
    return media, candidate, download, payload


def _torrent(info_hash, *, progress=1, state="pausedUP"):
    return QbTorrent(
        hash=info_hash, name="Example", size=20_000,
        progress=progress, ratio=1, state=state,
    )


@pytest.mark.asyncio
async def test_completed_missing_torrent_is_retired_and_missing_media_can_search(session_factory):
    async with session_factory() as session:
        media, _, download, _ = await _finished_download(session)
        media.next_search_at = utc_now() + timedelta(days=7)
        original_completed_at = download.completed_at
        original_submitted_at = download.submitted_at
        await session.commit()

        assert await sync_download_statuses(
            session, cast(QbittorrentAdapter, StatusQb([]))
        ) == 1
        assert download.state == DownloadState.COMPLETED
        assert download.cleanup_state == DownloadCleanupState.VANISHED
        assert download.info_hash is not None
        assert download.completed_at == original_completed_at
        assert download.submitted_at == original_submitted_at
        assert media.state == MediaState.READY
        assert media.next_search_at is None
        log = await session.scalar(
            select(ActivityLog).where(ActivityLog.event == "DOWNLOAD_RECORD_RETIRED")
        )
        assert log is not None
        assert log.details["info_hash"] == download.info_hash
        # Retirement is a single event, and does not count as our deleting files.
        assert download.cleanup_deleted_at is None
        assert await sync_download_statuses(
            session, cast(QbittorrentAdapter, StatusQb([]))
        ) == 0


@pytest.mark.asyncio
async def test_retiring_completed_torrent_keeps_confirmed_item_complete(session_factory):
    async with session_factory() as session:
        media, _, download, _ = await _finished_download(session, confirmed=True)
        confirmation = media.library_confirmed_at
        await sync_download_statuses(session, cast(QbittorrentAdapter, StatusQb([])))
        assert media.library_confirmed_at == confirmation
        assert media.state == MediaState.COMPLETE
        assert download.cleanup_state == DownloadCleanupState.VANISHED


@pytest.mark.asyncio
async def test_completed_torrent_still_in_qb_is_not_retired(session_factory):
    async with session_factory() as session:
        media, _, download, _ = await _finished_download(session)
        await sync_download_statuses(
            session, cast(QbittorrentAdapter, StatusQb([_torrent(download.info_hash)]))
        )
        assert download.cleanup_state == DownloadCleanupState.NONE
        assert media.state == MediaState.COMPLETE


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup", [DownloadCleanupState.DELETED, DownloadCleanupState.VANISHED])
@pytest.mark.parametrize("explicit_retry", [False, True])
async def test_retired_same_candidate_and_hash_make_a_new_submission_preserving_history(
    session_factory, cleanup, explicit_retry
):
    async with session_factory() as session:
        _, candidate, old, payload = await _finished_download(session, cleanup=cleanup)
        old_submitted_at = old.submitted_at
        old_completed_at = old.completed_at
        old_deleted_at = old.cleanup_deleted_at
        qb = CapturingQb()
        options = {
            "pt_factory": lambda _site: FakeTorrentSource(payload),
            "qb_factory": lambda: qb,
        }
        if explicit_retry:
            fresh = await retry_download(session, download_id=old.id, **options)
        else:
            fresh = await submit_download(
                session, candidate_id=candidate.id, confirm_warnings=True, **options
            )
        assert qb.add_kwargs
        assert fresh.id != old.id
        assert fresh.candidate_id == old.candidate_id
        assert fresh.info_hash == old.info_hash
        assert fresh.state == DownloadState.QUEUED
        assert fresh.progress == 0
        assert fresh.cleanup_state == DownloadCleanupState.NONE
        assert fresh.cleanup_deleted_at is None
        assert old.state == DownloadState.COMPLETED
        assert old.cleanup_state == cleanup
        assert old.submitted_at == old_submitted_at
        assert old.completed_at == old_completed_at
        assert old.cleanup_deleted_at == old_deleted_at
        again = await queue_download(session, candidate_id=candidate.id, confirm_warnings=True)
        assert again.id == fresh.id


async def _next_season(session, media, original):
    media.media_type = MediaType.TV
    original.season_coverage = [1]
    original.title = "Example.S01.COMPLETE.1080p"
    candidate = ReleaseCandidate(
        search_id=original.search_id,
        site_id=original.site_id,
        torrent_id="season-two",
        title="Example.S02.COMPLETE.1080p",
        score=0.9,
        season_coverage=[2],
        info_hash="b" * 40,
    )
    session.add(candidate)
    await session.commit()
    return candidate


@pytest.mark.asyncio
async def test_completed_seeding_season_does_not_block_next_season(session_factory):
    async with session_factory() as session:
        media, candidate, old, _ = await _finished_download(session)
        old.state = DownloadState.SEEDING
        next_season = await _next_season(session, media, candidate)
        fresh = await queue_download(
            session, candidate_id=next_season.id, confirm_warnings=True
        )
        assert fresh.id != old.id
        assert fresh.state == DownloadState.SUBMITTING
        assert old.state == DownloadState.SEEDING


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "progress"),
    [
        (DownloadState.SUBMITTING, 1),
        (DownloadState.OUTCOME_UNKNOWN, 1),
        (DownloadState.DOWNLOADING, 0.5),
        (DownloadState.PAUSED, 0.5),
        (DownloadState.SEEDING, 0.5),
    ],
)
async def test_unfinished_or_uncertain_download_still_blocks_parallel_season(
    session_factory, state, progress
):
    async with session_factory() as session:
        media, candidate, old, _ = await _finished_download(session)
        old.state = state
        old.progress = progress
        next_season = await _next_season(session, media, candidate)
        with pytest.raises(AppError) as caught:
            await queue_download(session, candidate_id=next_season.id, confirm_warnings=True)
        assert caught.value.error_code == "MEDIA_DOWNLOAD_ACTIVE"


@pytest.mark.asyncio
@pytest.mark.parametrize("old_present", [False, True])
async def test_old_season_keeps_newer_active_transfer(session_factory, old_present):
    async with session_factory() as session:
        media, candidate, old, _ = await _finished_download(session)
        next_season = await _next_season(session, media, candidate)
        active = await queue_download(session, candidate_id=next_season.id, confirm_warnings=True)
        active.state = DownloadState.DOWNLOADING
        active.progress = 0.5
        active.submitted_at = utc_now() - timedelta(days=1)
        await session.commit()
        torrents = [_torrent(active.info_hash, progress=0.5, state="downloading")]
        if old_present:
            torrents.append(_torrent(old.info_hash))
        await sync_download_statuses(session, cast(QbittorrentAdapter, StatusQb(torrents)))
        assert media.state == MediaState.DOWNLOADING
        assert active.state == DownloadState.DOWNLOADING


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state", [DownloadState.SUBMITTING, DownloadState.OUTCOME_UNKNOWN, DownloadState.DOWNLOADING]
)
async def test_retrying_failed_season_still_checks_another_active_season(session_factory, state):
    async with session_factory() as session:
        media, candidate, old, _ = await _finished_download(session)
        old.state = DownloadState.ERROR
        old.progress = 0
        old.error_message = "earlier failure"
        next_season = await _next_season(session, media, candidate)
        active = await queue_download(session, candidate_id=next_season.id, confirm_warnings=True)
        active.state = state
        await session.commit()
        with pytest.raises(AppError) as caught:
            await queue_download(session, candidate_id=candidate.id, confirm_warnings=True)
        assert caught.value.error_code == "MEDIA_DOWNLOAD_ACTIVE"
        assert old.state == DownloadState.ERROR
        assert old.error_message == "earlier failure"
        assert active.state == state


class AlreadyPresentQb(CapturingQb):
    async def add_torrent(self, _payload: bytes, **kwargs: object) -> QbAddResult:
        self.add_kwargs = kwargs
        return QbAddResult(info_hash=str(kwargs["expected_info_hash"]), outcome="ALREADY_PRESENT")


@pytest.mark.asyncio
@pytest.mark.parametrize("already_present", [False, True])
async def test_submission_audit_counts_only_actual_additions(session_factory, already_present):
    async with session_factory() as session:
        payload, info_hash = torrent_fixture()
        _, candidate = await add_candidate(
            session, source_item_id="audit-submission", info_hash=info_hash
        )
        qb = AlreadyPresentQb() if already_present else CapturingQb()
        with audit.scope(details={"job_id": "audit-job"}):
            download = await submit_download(
                session,
                candidate_id=candidate.id,
                confirm_warnings=True,
                pt_factory=lambda _site: FakeTorrentSource(payload),
                qb_factory=lambda: qb,
            )
        event = "DOWNLOAD_ALREADY_PRESENT" if already_present else "DOWNLOAD_SUBMITTED"
        log = await session.scalar(select(ActivityLog).where(ActivityLog.event == event))
        assert log is not None
        assert log.details["job_id"] == "audit-job"
        assert log.details["download_id"] == download.id
        if already_present:
            assert download.submitted_at is None
            assert await session.scalar(
                select(ActivityLog).where(ActivityLog.event == "DOWNLOAD_SUBMITTED")
            ) is None
        else:
            assert download.submitted_at is not None
