"""One click from a media item to a submitted download.

Filling one item by hand used to be: open it, run a search, read the
candidates, pick one, confirm its warnings.  The picking logic already
existed -- ``_choose_candidate``, the same one the scheduler runs -- but only
the scheduler could reach it.

Choosing by hand stays: quick fill runs an ordinary search, so every candidate
is still listed afterwards for the operator to override the pick.
"""

from __future__ import annotations

from typing import cast

import pytest
from sqlalchemy import select

from app.adapters.base import PtSiteAdapter
from app.adapters.downloaders.qbittorrent import QbittorrentAdapter
from app.adapters.pt_sites.avistaz import AvistaZMockAdapter
from app.errors import AppError
from app.models.enums import MediaType
from app.schemas.adapters import TorrentCandidate
from app.simple import automation as automation_module
from app.simple.automation import quick_fill_media, update_policy
from app.simple.models import (
    Download,
    DownloadState,
    LibraryMediaItem,
    MediaState,
    ReleaseCandidate,
    ReleaseSearch,
)
from app.simple.schemas import AutomationPolicyUpdate


def _candidate(tmdb_id: int, torrent_id: str, *, seeders: int = 8) -> TorrentCandidate:
    return TorrentCandidate(
        site_id="avistaz",
        torrent_id=torrent_id,
        release_title=f"Quick.{tmdb_id}.2026.1080p.WEB-DL",
        details_ref=f"avistaz:details:{torrent_id}",
        media_type=MediaType.MOVIE,
        tmdb_id=tmdb_id,
        resolution="1080p",
        source="WEB-DL",
        size_bytes=2_000_000,
        seeders=seeders,
        hit_and_run=False,
    )


async def _media(session, *, tmdb_id: int) -> LibraryMediaItem:
    media = LibraryMediaItem(
        source_item_id=f"quick-{tmdb_id}",
        media_type=MediaType.MOVIE,
        tmdb_id=tmdb_id,
        title=f"Quick {tmdb_id}",
        year=2026,
        state=MediaState.READY,
    )
    session.add(media)
    await session.commit()
    return media


def _fake_submit(submitted: list[str]):
    async def submit(download_session, *, candidate_id: str, **kwargs: object) -> Download:
        candidate = await download_session.get(ReleaseCandidate, candidate_id)
        assert candidate is not None
        search = await download_session.get(ReleaseSearch, candidate.search_id)
        assert search is not None
        download = Download(
            media_id=search.media_id,
            candidate_id=candidate.id,
            name=candidate.title,
            state=DownloadState.QUEUED,
        )
        download_session.add(download)
        media = await download_session.get(LibraryMediaItem, search.media_id)
        assert media is not None
        media.state = MediaState.DOWNLOADING
        await download_session.commit()
        submitted.append(candidate_id)
        return download

    return submit


def _adapters(adapter: AvistaZMockAdapter):
    return {
        "adapter_factory": lambda _site_id: adapter,
        "pt_factory": lambda _site_id: cast(PtSiteAdapter, object()),
        "qb_factory": lambda: cast(QbittorrentAdapter, object()),
    }


@pytest.mark.asyncio
async def test_one_call_searches_picks_and_submits(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        media = await _media(session, tmdb_id=1200)
        await update_policy(session, AutomationPolicyUpdate(minimum_score=0.5))
        submitted: list[str] = []
        monkeypatch.setattr(automation_module, "submit_download", _fake_submit(submitted))
        adapter = AvistaZMockAdapter(fixtures=[_candidate(1200, "quick-one")])

        search, selected, _, download = await quick_fill_media(
            session, media_id=media.id, **_adapters(adapter)
        )

        assert selected is not None
        assert download is not None
        assert len(submitted) == 1
        await session.refresh(media)
        assert media.state == MediaState.DOWNLOADING
        # The search is an ordinary one -- its candidates stay listed.
        stored = list(
            await session.scalars(
                select(ReleaseCandidate).where(ReleaseCandidate.search_id == search.id)
            )
        )
        assert stored


@pytest.mark.asyncio
async def test_every_candidate_stays_available_for_manual_choice(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shortcut must not hide the alternatives it did not pick."""

    async with session_factory() as session:
        media = await _media(session, tmdb_id=1201)
        await update_policy(session, AutomationPolicyUpdate(minimum_score=0.5))
        monkeypatch.setattr(automation_module, "submit_download", _fake_submit([]))
        adapter = AvistaZMockAdapter(
            fixtures=[
                _candidate(1201, "quick-a", seeders=20),
                _candidate(1201, "quick-b", seeders=3),
                _candidate(1201, "quick-c", seeders=1),
            ]
        )

        search, selected, _, _ = await quick_fill_media(
            session, media_id=media.id, **_adapters(adapter)
        )

        stored = list(
            await session.scalars(
                select(ReleaseCandidate).where(ReleaseCandidate.search_id == search.id)
            )
        )
        assert len(stored) == 3
        assert selected is not None
        assert selected.id in {item.id for item in stored}


@pytest.mark.asyncio
async def test_nothing_acceptable_reports_the_reasons_and_submits_nothing(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        media = await _media(session, tmdb_id=1202)
        await update_policy(session, AutomationPolicyUpdate(minimum_score=0.99))
        submitted: list[str] = []
        monkeypatch.setattr(automation_module, "submit_download", _fake_submit(submitted))
        adapter = AvistaZMockAdapter(fixtures=[_candidate(1202, "quick-low")])

        _, selected, rejected, download = await quick_fill_media(
            session, media_id=media.id, **_adapters(adapter)
        )

        assert selected is None
        assert download is None
        assert submitted == []
        assert any("评分低于" in reason for item in rejected for reason in item["reasons"])


@pytest.mark.asyncio
async def test_an_empty_search_records_the_cooldown(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A miss here backs off exactly as a scheduled miss does."""

    async with session_factory() as session:
        media = await _media(session, tmdb_id=1203)
        await update_policy(session, AutomationPolicyUpdate(minimum_score=0.5))
        monkeypatch.setattr(automation_module, "submit_download", _fake_submit([]))

        _, selected, _, _ = await quick_fill_media(
            session, media_id=media.id, **_adapters(AvistaZMockAdapter(fixtures=[]))
        )

        assert selected is None
        await session.refresh(media)
        assert media.search_miss_count == 1
        assert media.next_search_at is not None


@pytest.mark.asyncio
async def test_a_per_media_score_override_is_honoured(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        media = await _media(session, tmdb_id=1204)
        await update_policy(session, AutomationPolicyUpdate(minimum_score=0.99))
        media.minimum_score_override = 0.3
        await session.commit()
        submitted: list[str] = []
        monkeypatch.setattr(automation_module, "submit_download", _fake_submit(submitted))
        adapter = AvistaZMockAdapter(fixtures=[_candidate(1204, "quick-override")])

        _, selected, _, _ = await quick_fill_media(
            session, media_id=media.id, **_adapters(adapter)
        )

        assert selected is not None
        assert len(submitted) == 1


@pytest.mark.asyncio
async def test_an_unknown_media_is_refused(session_factory) -> None:
    async with session_factory() as session:
        with pytest.raises(AppError) as excinfo:
            await quick_fill_media(
                session, media_id="nope", **_adapters(AvistaZMockAdapter(fixtures=[]))
            )

        assert excinfo.value.error_code == "MEDIA_NOT_FOUND"


@pytest.mark.asyncio
async def test_an_unidentified_media_without_a_provider_is_refused(session_factory) -> None:
    async with session_factory() as session:
        media = await _media(session, tmdb_id=1205)
        media.tmdb_id = None
        await session.commit()

        with pytest.raises(AppError) as excinfo:
            await quick_fill_media(
                session,
                media_id=media.id,
                metadata_factory=None,
                **_adapters(AvistaZMockAdapter(fixtures=[])),
            )

        assert excinfo.value.error_code == "MEDIA_IDENTITY_REQUIRED"
