"""End-to-end automation flow, and the scheduling rules that gate it.

Optimisation 6 in ``NEXT_OPTIMIZATION_PLAN.md``.  Two of the three tests the
plan sketched were left as ``pass``; the cooldown one covered a genuine gap --
``test_search_cooldown.py`` proved ``_search_cooldown`` returns the right
timedelta, but nothing proved ``run_automation`` actually skips a media item
sitting in cooldown.  The concurrency one does not apply: jobs run one at a
time (see ``tests/test_rate_limit_sharing.py``).
"""

from __future__ import annotations

from datetime import timedelta
from typing import cast

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import PtSiteAdapter
from app.adapters.downloaders.qbittorrent import QbittorrentAdapter
from app.adapters.pt_sites.avistaz import AvistaZMockAdapter
from app.core.time import utc_now
from app.models.enums import MediaType
from app.schemas.adapters import TorrentCandidate, TorrentSearchRequest
from app.simple import automation as automation_module
from app.simple.automation import run_automation, run_dry_run, update_policy
from app.simple.models import (
    AutomationJob,
    AutomationJobState,
    Download,
    DownloadState,
    LibraryMediaItem,
    MediaState,
    ReleaseCandidate,
    ReleaseSearch,
    SearchState,
)
from app.simple.schemas import AutomationPolicyUpdate


def movie_candidate(tmdb_id: int, torrent_id: str) -> TorrentCandidate:
    return TorrentCandidate(
        site_id="avistaz",
        torrent_id=torrent_id,
        release_title=f"Flow.{tmdb_id}.2026.1080p.WEB-DL",
        details_ref=f"avistaz:details:{torrent_id}",
        media_type=MediaType.MOVIE,
        tmdb_id=tmdb_id,
        resolution="1080p",
        source="WEB-DL",
        size_bytes=2_000_000,
        seeders=8,
        hit_and_run=False,
    )


class CountingAdapter(AvistaZMockAdapter):
    """A site that records every search it is asked to perform."""

    def __init__(self, fixtures: list[TorrentCandidate] | None = None) -> None:
        super().__init__(fixtures=fixtures or [])
        self.searches = 0

    async def search(self, request: TorrentSearchRequest) -> list[TorrentCandidate]:
        self.searches += 1
        return await super().search(request)


async def _make_media(session, *, tmdb_id: int, suffix: str) -> LibraryMediaItem:
    media = LibraryMediaItem(
        source_item_id=f"flow-{suffix}",
        media_type=MediaType.MOVIE,
        tmdb_id=tmdb_id,
        title=f"Flow Movie {tmdb_id}",
        year=2026,
        state=MediaState.READY,
    )
    session.add(media)
    await session.commit()
    return media


# ---------------------------------------------------------------------------
# The full path: missing media -> search -> candidate -> qBittorrent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_missing_media_travels_all_the_way_to_a_submitted_download(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        media = await _make_media(session, tmdb_id=700, suffix="end-to-end")
        await update_policy(
            session,
            AutomationPolicyUpdate(enabled=True, dry_run=False, minimum_score=0.5),
        )
        submitted: list[str] = []

        async def fake_submit(
            download_session: AsyncSession, *, candidate_id: str, **kwargs: object
        ) -> Download:
            candidate = await download_session.get(ReleaseCandidate, candidate_id)
            assert candidate is not None
            search = await download_session.get(ReleaseSearch, candidate.search_id)
            assert search is not None
            download = Download(
                media_id=search.media_id,
                candidate_id=candidate.id,
                name=candidate.title,
                info_hash="f" * 40,
                state=DownloadState.QUEUED,
                submitted_at=utc_now(),
            )
            download_session.add(download)
            item = await download_session.get(LibraryMediaItem, search.media_id)
            assert item is not None
            item.state = MediaState.DOWNLOADING
            await download_session.commit()
            callback = kwargs.get("on_download")
            assert callable(callback)
            await callback(download)
            submitted.append(candidate_id)
            return download

        monkeypatch.setattr(automation_module, "submit_download", fake_submit)
        adapter = CountingAdapter([movie_candidate(700, "flow-one")])

        _, created, succeeded, failed = await run_automation(
            session,
            adapter_factory=lambda _site_id: adapter,
            pt_factory=lambda _site_id: cast(PtSiteAdapter, object()),
            qb_factory=lambda: cast(QbittorrentAdapter, object()),
        )

        assert (created, succeeded, failed) == (1, 1, 0)
        assert len(submitted) == 1

        job = await session.scalar(select(AutomationJob).where(AutomationJob.media_id == media.id))
        assert job is not None
        assert job.state == AutomationJobState.SUCCEEDED
        assert job.error_code is None
        assert job.search_id is not None
        assert job.selected_candidate_id is not None

        search = await session.get(ReleaseSearch, job.search_id)
        assert search is not None
        assert search.state == SearchState.SUCCEEDED
        # Every search now carries a cache stamp so the next identical one can
        # reuse it.
        assert search.cache_key is not None
        assert search.cache_expires_at is not None

        download = await session.scalar(select(Download).where(Download.media_id == media.id))
        assert download is not None
        assert download.state == DownloadState.QUEUED

        await session.refresh(media)
        assert media.state == MediaState.DOWNLOADING
        # A hit resets the backoff.
        assert media.search_miss_count == 0
        assert media.next_search_at is None


# ---------------------------------------------------------------------------
# Cooldown -- the plan left this one as ``pass``
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_media_in_cooldown_is_skipped_by_the_next_run(session_factory) -> None:
    async with session_factory() as session:
        media = await _make_media(session, tmdb_id=701, suffix="cooldown")
        await update_policy(session, AutomationPolicyUpdate(enabled=True))
        # A site with nothing on it: the first run must miss.
        adapter = CountingAdapter([])

        _, created, _, _ = await run_dry_run(session, adapter_factory=lambda _site_id: adapter)
        assert created == 1
        # One job issues several queries (external id, then title fallback);
        # the number itself does not matter, only that it stops growing.
        searches_after_first_run = adapter.searches
        assert searches_after_first_run > 0
        await session.refresh(media)
        assert media.search_miss_count == 1
        assert media.next_search_at is not None

        _, created_again, _, _ = await run_dry_run(
            session, adapter_factory=lambda _site_id: adapter
        )

        assert created_again == 0
        # The point of the cooldown: not one more request to the site.
        assert adapter.searches == searches_after_first_run


@pytest.mark.asyncio
async def test_the_media_returns_once_the_cooldown_has_elapsed(session_factory) -> None:
    async with session_factory() as session:
        media = await _make_media(session, tmdb_id=702, suffix="cooldown-elapsed")
        await update_policy(session, AutomationPolicyUpdate(enabled=True))
        adapter = CountingAdapter([])

        await run_dry_run(session, adapter_factory=lambda _site_id: adapter)
        await session.refresh(media)
        media.next_search_at = utc_now() - timedelta(seconds=1)
        # In production the shortest cooldown (6h) far outlives the 5-minute
        # result cache, so expiring it here is what a real elapsed cooldown
        # looks like.  Leaving it live is covered by its own test below.
        for search in await session.scalars(select(ReleaseSearch)):
            search.cache_expires_at = utc_now() - timedelta(seconds=1)
        await session.commit()

        searches_before = adapter.searches
        _, created, _, _ = await run_dry_run(session, adapter_factory=lambda _site_id: adapter)

        assert created == 1
        assert adapter.searches > searches_before
        await session.refresh(media)
        assert media.search_miss_count == 2


@pytest.mark.asyncio
async def test_a_cooldown_cleared_inside_the_cache_window_still_costs_no_request(
    session_factory,
) -> None:
    """The two backoffs stack rather than fight.

    Only reachable when a cooldown is cleared by hand within five minutes --
    the shortest configurable tier is an hour -- but the ordering matters: the
    cache is consulted before the site, so a re-queued media item cannot turn
    into an extra request.
    """

    async with session_factory() as session:
        media = await _make_media(session, tmdb_id=706, suffix="cooldown-cached")
        await update_policy(session, AutomationPolicyUpdate(enabled=True))
        adapter = CountingAdapter([])

        await run_dry_run(session, adapter_factory=lambda _site_id: adapter)
        searches_before = adapter.searches
        await session.refresh(media)
        media.next_search_at = utc_now() - timedelta(seconds=1)
        await session.commit()

        _, created, _, _ = await run_dry_run(session, adapter_factory=lambda _site_id: adapter)

        assert created == 1
        assert adapter.searches == searches_before


@pytest.mark.asyncio
async def test_a_shorter_configured_cooldown_shortens_the_wait(session_factory) -> None:
    """The tiers are configurable; the run must honour the configured value."""

    async with session_factory() as session:
        media = await _make_media(session, tmdb_id=703, suffix="cooldown-configured")
        await update_policy(
            session,
            AutomationPolicyUpdate(
                enabled=True,
                cooldown_tier_1_hours=6,
                cooldown_tier_2_hours=24,
                cooldown_tier_3_hours=72,
            ),
        )
        adapter = CountingAdapter([])

        await run_dry_run(session, adapter_factory=lambda _site_id: adapter)

        await session.refresh(media)
        assert media.next_search_at is not None
        assert media.last_searched_at is not None
        assert media.next_search_at - media.last_searched_at == timedelta(hours=6)


# ---------------------------------------------------------------------------
# The two fixes from the review patch, exercised through a whole run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_stale_transient_failure_lets_the_media_run_again(session_factory) -> None:
    async with session_factory() as session:
        media = await _make_media(session, tmdb_id=704, suffix="released")
        await update_policy(session, AutomationPolicyUpdate(enabled=True, minimum_score=0.5))
        stale = AutomationJob(
            run_id="flow-old-run",
            media_id=media.id,
            state=AutomationJobState.FAILED,
            error_code="PT_TEMPORARY_FAILURE",
            error_message="站点 502",
            finished_at=utc_now() - timedelta(hours=7),
        )
        session.add(stale)
        await session.commit()
        adapter = CountingAdapter([movie_candidate(704, "flow-released")])

        _, created, succeeded, _ = await run_dry_run(
            session, adapter_factory=lambda _site_id: adapter
        )

        assert created == 1
        assert succeeded == 1
        await session.refresh(stale)
        assert stale.superseded_at is not None
        # The original run's recorded outcome is untouched.
        assert stale.state == AutomationJobState.FAILED


@pytest.mark.asyncio
async def test_a_failure_needing_a_human_keeps_the_media_parked(session_factory) -> None:
    async with session_factory() as session:
        media = await _make_media(session, tmdb_id=705, suffix="parked")
        await update_policy(session, AutomationPolicyUpdate(enabled=True))
        blocked = AutomationJob(
            run_id="flow-blocked-run",
            media_id=media.id,
            state=AutomationJobState.FAILED,
            error_code="TMDB_SELECTION_REQUIRED",
            finished_at=utc_now() - timedelta(days=30),
        )
        session.add(blocked)
        await session.commit()
        adapter = CountingAdapter([movie_candidate(705, "flow-parked")])

        _, created, _, _ = await run_dry_run(session, adapter_factory=lambda _site_id: adapter)

        assert created == 0
        assert adapter.searches == 0
        await session.refresh(blocked)
        assert blocked.superseded_at is None
