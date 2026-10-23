from __future__ import annotations

from datetime import timedelta
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import PtSiteAdapter
from app.adapters.downloaders.qbittorrent import QbittorrentAdapter
from app.adapters.pt_sites.avistaz import AvistaZMockAdapter
from app.core.time import utc_now
from app.models.enums import MediaType
from app.schemas.adapters import TorrentCandidate, TorrentSearchRequest
from app.simple import automation
from app.simple.automation_state import automation_run_outcomes
from app.simple.models import (
    AutomationJob,
    AutomationJobState,
    AutomationRun,
    AutomationRunState,
    Download,
    DownloadCleanupState,
    DownloadState,
    Episode,
    EpisodeState,
    LibraryMediaItem,
    MediaState,
    ReleaseCandidate,
    ReleaseSearch,
    SearchState,
)
from app.simple.schemas import AutomationPolicyUpdate


class RecordingAdapter(AvistaZMockAdapter):
    def __init__(self, fixtures: list[TorrentCandidate] | None = None) -> None:
        super().__init__(fixtures=fixtures)
        self.searched_ids: list[int] = []

    async def search(self, request: TorrentSearchRequest) -> list[TorrentCandidate]:
        if request.tmdb is not None:
            self.searched_ids.append(request.tmdb)
        return await super().search(request)


def make_media(number: int, *, tv: bool = False) -> LibraryMediaItem:
    return LibraryMediaItem(
        id=str(uuid4()),
        source_item_id=f"scheduling-{number}",
        media_type=MediaType.TV if tv else MediaType.MOVIE,
        tmdb_id=number,
        title=f"Scheduling Title {number}",
        year=2026,
        state=MediaState.READY,
    )


def result_candidate(
    item: LibraryMediaItem,
    torrent_id: str,
    *,
    season: int | None = None,
    seeders: int = 10,
    info_hash: str | None = None,
) -> TorrentCandidate:
    season_title = f" S{season:02d} Complete" if season is not None else ""
    return TorrentCandidate(
        site_id="avistaz",
        torrent_id=torrent_id,
        details_ref=f"avistaz:details:{torrent_id}",
        release_title=f"{item.title}{season_title} 2026 1080p WEB-DL",
        media_type=item.media_type,
        tmdb_id=item.tmdb_id,
        year=2026,
        season=season,
        collection_type="season_pack" if season is not None else None,
        resolution="1080p",
        source="WEB-DL",
        size_bytes=2_000_000,
        seeders=seeders,
        hit_and_run=False,
        info_hash=info_hash,
    )


async def store_download(
    session: AsyncSession,
    item: LibraryMediaItem,
    *,
    state: DownloadState = DownloadState.COMPLETED,
    cleanup: DownloadCleanupState = DownloadCleanupState.NONE,
    season: int | None = None,
    torrent_id: str | None = None,
    info_hash: str | None = None,
) -> tuple[Download, ReleaseCandidate]:
    search = ReleaseSearch(
        id=str(uuid4()), media_id=item.id, site_ids=["avistaz"], state=SearchState.SUCCEEDED
    )
    release = ReleaseCandidate(
        id=str(uuid4()),
        search_id=search.id,
        site_id="avistaz",
        torrent_id=torrent_id or str(uuid4()),
        title=f"{item.title} S{season:02d} Complete" if season is not None else item.title,
        season_coverage=[season] if season is not None else [],
        episode_coverage=[],
        score=1,
        info_hash=info_hash,
    )
    existing = Download(
        id=str(uuid4()),
        media_id=item.id,
        candidate_id=release.id,
        name=release.title,
        state=state,
        cleanup_state=cleanup,
        info_hash=info_hash,
        submitted_at=utc_now() - timedelta(days=10),
    )
    session.add_all([search, release, existing])
    return existing, release


async def enable(session: AsyncSession, *, live: bool = False) -> None:
    await automation.update_policy(
        session,
        AutomationPolicyUpdate(enabled=True, dry_run=not live, minimum_score=0.5),
    )


async def run_recorded(
    session: AsyncSession, adapter: RecordingAdapter, *, limit: int = 20
) -> AutomationRun:
    run = await automation.create_automation_run(session, trigger="scheduled")
    await automation.run_automation(
        session, adapter_factory=lambda _site: adapter, run_record=run, limit=limit
    )
    await session.refresh(run)
    return run


async def test_many_completed_legacy_titles_do_not_starve_fresh_searches(session_factory):
    async with session_factory() as session:
        old = [make_media(number, tv=True) for number in range(1000, 1025)]
        fresh = [make_media(2000), make_media(2001)]
        for item in old:
            item.updated_at = utc_now() - timedelta(days=10)
        session.add_all([*old, *fresh])
        for item in old:
            await store_download(session, item, season=1)
        await session.commit()
        await enable(session)
        adapter = RecordingAdapter()
        run = await run_recorded(session, adapter, limit=2)
        jobs = list(
            await session.scalars(select(AutomationJob).where(AutomationJob.run_id == run.id))
        )
        assert {job.media_id for job in jobs} == {item.id for item in fresh}
        assert set(adapter.searched_ids) == {2000, 2001}
        assert run.created_count == 2
        assert run.awaiting_library_count == 25
        assert run.skipped_existing_count == 0


@pytest.mark.parametrize(
    "state", [DownloadState.DOWNLOADING, DownloadState.SUBMITTING, DownloadState.OUTCOME_UNKNOWN]
)
async def test_active_or_unconfirmed_download_is_skipped_without_a_fresh_job(
    session_factory, state
):
    async with session_factory() as session:
        item = make_media(3000)
        session.add(item)
        await store_download(session, item, state=state)
        await session.commit()
        await enable(session)
        adapter = RecordingAdapter()
        run = await run_recorded(session, adapter)
        assert run.created_count == 0
        assert run.skipped_existing_count == 1
        assert run.awaiting_library_count == 0
        assert not adapter.searched_ids
        assert (
            await session.scalar(select(AutomationJob).where(AutomationJob.run_id == run.id))
            is None
        )


async def test_search_fairness_uses_last_search_not_nextfind_update_time(session_factory):
    async with session_factory() as session:
        never, oldest, recent = [make_media(number) for number in (3100, 3101, 3102)]
        now = utc_now()
        never.updated_at = now
        oldest.last_searched_at = now - timedelta(days=10)
        oldest.updated_at = now
        recent.last_searched_at = now - timedelta(days=1)
        recent.updated_at = now - timedelta(days=30)
        session.add_all([recent, oldest, never])
        await session.commit()
        await enable(session)
        adapter = RecordingAdapter()
        run = await run_recorded(session, adapter, limit=2)
        jobs = list(
            await session.scalars(select(AutomationJob).where(AutomationJob.run_id == run.id))
        )
        assert {job.media_id for job in jobs} == {never.id, oldest.id}
        assert list(dict.fromkeys(adapter.searched_ids)) == [3100, 3101]
        assert recent.last_searched_at == now - timedelta(days=1)


async def test_explicit_new_season_is_searched_and_old_better_seeded_pack_is_rejected(
    session_factory,
):
    async with session_factory() as session:
        item = make_media(3200, tv=True)
        session.add(item)
        await store_download(session, item, season=1)
        session.add_all(
            Episode(
                media_id=item.id, season_number=number, episode_number=1, state=EpisodeState.MISSING
            )
            for number in (1, 2)
        )
        await session.commit()
        await enable(session)
        old_result = result_candidate(item, "old-high-seeders", season=1, seeders=100)
        new_result = result_candidate(item, "new-season", season=2, seeders=5)
        adapter = RecordingAdapter([old_result, new_result])
        run = await run_recorded(session, adapter)
        job = await session.scalar(select(AutomationJob).where(AutomationJob.run_id == run.id))
        assert job is not None and job.state == AutomationJobState.SUCCEEDED
        selected = await session.get(ReleaseCandidate, job.selected_candidate_id)
        assert selected is not None and selected.torrent_id == "new-season"
        assert run.awaiting_library_count == 0
        assert job.decision["outcome"] == "dry_run"
        assert any(entry["title"] == old_result.release_title for entry in job.decision["rejected"])


@pytest.mark.parametrize("identity", ["torrent_id", "info_hash"])
async def test_known_torrent_identity_is_excluded_even_if_reported_coverage_changed(
    session_factory, identity
):
    async with session_factory() as session:
        item = make_media(3250, tv=True)
        session.add(item)
        old_hash = "a" * 40
        # This historical release had no usable coverage. A later search with
        # different labels must not cause the same torrent to be added again.
        await store_download(session, item, torrent_id="already-downloaded", info_hash=old_hash)
        session.add(
            Episode(media_id=item.id, season_number=2, episode_number=1, state=EpisodeState.MISSING)
        )
        await session.commit()
        await enable(session)
        duplicate = result_candidate(
            item,
            "already-downloaded" if identity == "torrent_id" else "renamed-reference",
            season=2,
            seeders=100,
            info_hash=old_hash if identity == "info_hash" else None,
        )
        fresh = result_candidate(item, "genuinely-new", season=2, seeders=5, info_hash="b" * 40)
        run = await run_recorded(session, RecordingAdapter([duplicate, fresh]))
        job = await session.scalar(select(AutomationJob).where(AutomationJob.run_id == run.id))
        assert job is not None and job.state == AutomationJobState.SUCCEEDED
        selected = await session.get(ReleaseCandidate, job.selected_candidate_id)
        assert selected is not None and selected.torrent_id == "genuinely-new"


async def test_search_without_eligible_release_records_no_match_and_cooldown(session_factory):
    async with session_factory() as session:
        item = make_media(3300)
        session.add(item)
        await session.commit()
        await enable(session)
        run = await run_recorded(session, RecordingAdapter())
        job = await session.scalar(select(AutomationJob).where(AutomationJob.run_id == run.id))
        assert job is not None and job.state == AutomationJobState.SUCCEEDED
        assert job.decision["outcome"] == "no_match"
        assert job.selected_candidate_id is None
        assert job.download_id is None
        assert item.search_miss_count == 1
        assert item.last_searched_at is not None and item.next_search_at is not None


async def test_dry_run_selection_has_its_own_outcome_and_never_creates_download(session_factory):
    async with session_factory() as session:
        item = make_media(3400)
        session.add(item)
        await session.commit()
        await enable(session)
        run = await run_recorded(session, RecordingAdapter([result_candidate(item, "dry-run")]))
        job = await session.scalar(select(AutomationJob).where(AutomationJob.run_id == run.id))
        assert job is not None and job.state == AutomationJobState.SUCCEEDED
        assert job.decision["outcome"] == "dry_run"
        assert job.selected_candidate_id is not None
        assert job.download_id is None
        assert await session.scalar(select(Download)) is None


async def test_due_retry_for_completed_download_does_not_take_fresh_search_slot(session_factory):
    async with session_factory() as session:
        old, fresh = make_media(3500), make_media(3501)
        session.add_all([old, fresh])
        existing, release = await store_download(session, old)
        previous_run = AutomationRun(id=str(uuid4()), state=AutomationRunState.SUCCEEDED)
        source = AutomationJob(
            run_id=previous_run.id,
            media_id=old.id,
            state=AutomationJobState.RETRY_WAIT,
            next_attempt_at=utc_now() - timedelta(minutes=1),
            selected_candidate_id=release.id,
            download_id=existing.id,
        )
        session.add_all([previous_run, source])
        await session.commit()
        await enable(session)
        adapter = RecordingAdapter()
        run = await run_recorded(session, adapter, limit=1)
        assert set(adapter.searched_ids) == {3501}
        assert (
            await session.scalar(
                select(AutomationJob).where(
                    AutomationJob.run_id == run.id, AutomationJob.media_id == fresh.id
                )
            )
            is not None
        )
        assert source.run_id == previous_run.id
        outcomes = await automation_run_outcomes(session, [run])
        assert outcomes[run.id]["awaiting_library"] == 1


async def test_explicit_retry_of_unknown_submission_remains_an_explicit_failure(session_factory):
    async with session_factory() as session:
        item = make_media(3600)
        session.add(item)
        existing, release = await store_download(session, item, state=DownloadState.OUTCOME_UNKNOWN)
        previous_run = AutomationRun(id=str(uuid4()), state=AutomationRunState.FAILED)
        source = AutomationJob(
            run_id=previous_run.id,
            media_id=item.id,
            state=AutomationJobState.FAILED,
            selected_candidate_id=release.id,
            download_id=existing.id,
            error_code="QB_ADD_OUTCOME_UNKNOWN",
        )
        session.add_all([previous_run, source])
        await session.commit()
        await enable(session, live=True)
        retry, run = await automation.retry_job(session, source.id)
        adapter = RecordingAdapter()
        await automation.run_automation(
            session,
            adapter_factory=lambda _site: adapter,
            pt_factory=lambda _site: cast(PtSiteAdapter, object()),
            qb_factory=lambda: cast(QbittorrentAdapter, object()),
            run_record=run,
        )
        await session.refresh(retry)
        assert retry.state == AutomationJobState.FAILED
        assert retry.error_code == "QB_ADD_OUTCOME_UNKNOWN"
        assert run.failed_count == 1
        assert not adapter.searched_ids


async def test_retry_links_uncertain_download_instead_of_older_completed_season(session_factory):
    async with session_factory() as session:
        item = make_media(3601, tv=True)
        session.add(item)
        old, _ = await store_download(session, item, season=1)
        await session.flush()
        uncertain, release = await store_download(
            session,
            item,
            state=DownloadState.OUTCOME_UNKNOWN,
            season=2,
        )
        previous_run = AutomationRun(id=str(uuid4()), state=AutomationRunState.FAILED)
        source = AutomationJob(
            run_id=previous_run.id,
            media_id=item.id,
            state=AutomationJobState.FAILED,
            selected_candidate_id=release.id,
            download_id=uncertain.id,
        )
        session.add_all([previous_run, source])
        await session.commit()
        await enable(session, live=True)
        retry, run = await automation.retry_job(session, source.id)
        await automation.run_automation(
            session,
            adapter_factory=lambda _: RecordingAdapter(),
            pt_factory=lambda _: cast(PtSiteAdapter, object()),
            qb_factory=lambda: cast(QbittorrentAdapter, object()),
            run_record=run,
        )
        assert retry.download_id == uncertain.id and retry.download_id != old.id
        assert retry.error_code == "QB_ADD_OUTCOME_UNKNOWN"


async def test_confirmed_library_retry_does_not_consume_a_fresh_search_slot(session_factory):
    async with session_factory() as session:
        confirmed, fresh = make_media(3602), make_media(3603)
        confirmed.library_confirmed_at = utc_now()
        session.add_all([confirmed, fresh])
        await session.flush()
        source = AutomationJob(
            run_id=str(uuid4()),
            media_id=confirmed.id,
            state=AutomationJobState.RETRY_WAIT,
            next_attempt_at=utc_now() - timedelta(minutes=1),
        )
        session.add(source)
        await session.commit()
        await enable(session)
        adapter = RecordingAdapter()
        run = await run_recorded(session, adapter, limit=1)
        assert set(adapter.searched_ids) == {3603}
        jobs = list(
            await session.scalars(
                select(AutomationJob).where(
                    AutomationJob.run_id == run.id,
                    AutomationJob.media_id == confirmed.id,
                )
            )
        )
        assert len(jobs) == 1 and jobs[0].decision["outcome"] == "skipped"


@pytest.mark.parametrize(
    "state,cleanup",
    [
        (DownloadState.ERROR, DownloadCleanupState.NONE),
        (DownloadState.COMPLETED, DownloadCleanupState.DELETED),
        (DownloadState.COMPLETED, DownloadCleanupState.VANISHED),
    ],
)
async def test_failed_and_removed_downloads_remain_eligible_for_search(
    session_factory, state, cleanup
):
    async with session_factory() as session:
        item = make_media(3700)
        session.add(item)
        await store_download(session, item, state=state, cleanup=cleanup)
        await session.commit()
        await enable(session)
        adapter = RecordingAdapter([result_candidate(item, "replacement")])
        run = await run_recorded(session, adapter)
        assert run.created_count == 1
        assert run.skipped_existing_count == 0
        assert run.awaiting_library_count == 0
        assert set(adapter.searched_ids) == {3700}
