"""Searches and NextFind syncs run in the background; clients poll for them."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.base import MediaSourceAdapter
from app.adapters.pt_sites.avistaz import AvistaZMockAdapter
from app.api.dependencies import get_operator_principal, get_viewer_principal
from app.core.auth import Principal
from app.core.time import utc_now
from app.db.session import get_session
from app.errors import AppError
from app.main import app
from app.models.enums import AuthRole, IdentityConfidence, MediaType, MetadataStatus
from app.schemas.adapters import (
    AdapterManifest,
    LibraryDetails,
    MediaDiscoveryResult,
    MediaItemData,
    ProbeResult,
    TorrentCandidate,
)
from app.simple import background, integrations
from app.simple import routes as simple_routes
from app.simple.models import (
    LibraryMediaItem,
    MediaState,
    ReleaseCandidate,
    ReleaseSearch,
    SearchState,
)
from app.simple.service import start_search


@pytest.fixture(autouse=True)
def fresh_library_sync_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(integrations, "_library_sync", integrations.LibrarySyncStatus())


class OneShowNextFind(MediaSourceAdapter):
    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            id="nextfind",
            name="NextFind",
            adapter_type="media_source",
            version="test",
            enabled=True,
            mode="MOCK",
            description="test",
        )

    async def probe(self) -> ProbeResult:
        return ProbeResult(healthy=True, message="ok")

    async def authenticate(self) -> None:
        return None

    async def list_missing_media(self) -> MediaDiscoveryResult:
        now = utc_now()
        return MediaDiscoveryResult(
            items=[
                MediaItemData(
                    source="nextfind",
                    source_item_id="background-show",
                    media_type=MediaType.TV,
                    tmdb_id=3200,
                    title="Background Show",
                    country_codes=["JP"],
                    original_language="ja",
                    missing_episodes=["S01E01"],
                    identity_confidence=IdentityConfidence.HIGH,
                    metadata_status=MetadataStatus.RESOLVED,
                    discovered_at=now,
                    updated_at=now,
                )
            ]
        )

    async def get_library_details(self, media_type: MediaType, tmdb_id: int) -> LibraryDetails:
        return LibraryDetails(tmdb_id=tmdb_id, media_type=media_type)


class BrokenNextFind(OneShowNextFind):
    async def authenticate(self) -> None:
        raise AppError("NEXTFIND_AUTH_FAILED", "NextFind 登录失败", status_code=502)


def _candidate(tmdb_id: int) -> TorrentCandidate:
    return TorrentCandidate(
        site_id="avistaz",
        torrent_id="background-1",
        release_title=f"Background.{tmdb_id}.2026.1080p.WEB-DL",
        details_ref="avistaz:details:background-1",
        media_type=MediaType.MOVIE,
        tmdb_id=tmdb_id,
        resolution="1080p",
        source="WEB-DL",
        size_bytes=2_000_000,
        seeders=5,
        hit_and_run=False,
    )


async def _media(session: AsyncSession, *, tmdb_id: int) -> LibraryMediaItem:
    media = LibraryMediaItem(
        source_item_id=f"background-{tmdb_id}",
        media_type=MediaType.MOVIE,
        tmdb_id=tmdb_id,
        title="Background Movie",
        year=2026,
        state=MediaState.READY,
    )
    session.add(media)
    await session.commit()
    return media


def _no_tmdb() -> None:
    raise AppError("TMDB_NOT_CONFIGURED", "TMDB 尚未完成配置", status_code=409)


def _override(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async def session_override() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    async def operator_override() -> Principal:
        return Principal(
            username="operator",
            role=AuthRole.OPERATOR,
            issued_at=0,
            expires_at=2_000_000_000,
            csrf_digest="test",
        )

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_operator_principal] = operator_override
    app.dependency_overrides[get_viewer_principal] = operator_override


@pytest.mark.asyncio
async def test_a_search_already_in_flight_is_followed_not_repeated(session_factory) -> None:
    async with session_factory() as session:
        media = await _media(session, tmdb_id=3100)
        first, created = await start_search(session, media_id=media.id, site_ids=["avistaz"])
        again, created_again = await start_search(
            session, media_id=media.id, site_ids=["avistaz"], force=True
        )

    assert created is True
    assert created_again is False
    assert again.id == first.id


@pytest.mark.asyncio
async def test_a_background_search_stores_its_candidates(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        background,
        "build_pt_site",
        lambda _site_id, **_kwargs: AvistaZMockAdapter(fixtures=[_candidate(3101)]),
    )
    monkeypatch.setattr(background, "build_tmdb", _no_tmdb)
    async with session_factory() as session:
        media = await _media(session, tmdb_id=3101)
        search, _ = await start_search(session, media_id=media.id, site_ids=["avistaz"])

    await background.run_release_search_in_background(search.id, session_factory)

    async with session_factory() as session:
        stored = await session.get(ReleaseSearch, search.id)
        candidates = list(
            await session.scalars(
                select(ReleaseCandidate).where(ReleaseCandidate.search_id == search.id)
            )
        )
        refreshed = await session.get(LibraryMediaItem, media.id)
    assert stored is not None and stored.state == SearchState.SUCCEEDED
    assert len(candidates) == 1
    assert refreshed is not None and refreshed.state == MediaState.CANDIDATES


@pytest.mark.asyncio
async def test_a_search_that_fails_before_it_starts_is_still_marked_failed(
    session_factory,
) -> None:
    async with session_factory() as session:
        media = await _media(session, tmdb_id=3102)
        search, _ = await start_search(session, media_id=media.id, site_ids=["avistaz"])
        # The identity is gone by the time the background task gets to run.
        media.tmdb_id = None
        await session.commit()

    await background.run_release_search_in_background(search.id, session_factory)

    async with session_factory() as session:
        stored = await session.get(ReleaseSearch, search.id)
        refreshed = await session.get(LibraryMediaItem, media.id)
    assert stored is not None and stored.state == SearchState.FAILED
    assert stored.error_message == "请先确认 TMDB 影视信息"
    assert refreshed is not None and refreshed.state == MediaState.NEEDS_ATTENTION


@pytest.mark.asyncio
async def test_a_restart_fails_the_searches_it_cut_off(session_factory) -> None:
    async with session_factory() as session:
        media = await _media(session, tmdb_id=3103)
        search, _ = await start_search(session, media_id=media.id, site_ids=["avistaz"])

        assert await background.recover_interrupted_searches(session) == 1
        assert search.state == SearchState.FAILED
        assert media.state == MediaState.NEEDS_ATTENTION


@pytest.mark.asyncio
async def test_a_queued_library_sync_reports_running_then_its_result(session_factory) -> None:
    background.queue_library_sync(OneShowNextFind(), session_factory=session_factory)
    assert integrations.library_sync_status().state == "RUNNING"

    await asyncio.gather(*list(background._tasks))

    status = integrations.library_sync_status()
    assert (status.state, status.created, status.updated) == ("SUCCEEDED", 1, 0)
    assert status.finished_at is not None


@pytest.mark.asyncio
async def test_a_failed_library_sync_keeps_the_reason(session_factory) -> None:
    background.queue_library_sync(BrokenNextFind(), session_factory=session_factory)
    await asyncio.gather(*list(background._tasks))

    status = integrations.library_sync_status()
    assert status.state == "FAILED"
    assert status.error_message == "NextFind 登录失败"


@pytest.mark.asyncio
async def test_the_search_endpoint_answers_at_once_and_searches_in_the_background(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    queued: list[str] = []
    monkeypatch.setattr(simple_routes, "queue_release_search", queued.append)
    async with session_factory() as session:
        media = await _media(session, tmdb_id=3104)
    _override(session_factory)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            first = await client.post(f"/api/library/{media.id}/searches", json={})
            second = await client.post(
                f"/api/library/{media.id}/searches", json={"force": True}
            )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 200
    assert first.json()["state"] == "PENDING"
    # No sites named: the ones the automation policy uses.
    assert first.json()["site_ids"] == ["avistaz"]
    assert first.json()["candidates"] == []
    # A second click follows the search already under way.
    assert second.json()["id"] == first.json()["id"]
    assert queued == [first.json()["id"]]


@pytest.mark.asyncio
async def test_library_sync_is_started_once_and_can_be_polled(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[MediaSourceAdapter] = []

    def fake_queue(adapter: MediaSourceAdapter) -> None:
        started.append(adapter)
        integrations.mark_library_sync_started()

    monkeypatch.setattr(simple_routes, "build_nextfind", OneShowNextFind)
    monkeypatch.setattr(simple_routes, "queue_library_sync", fake_queue)
    _override(session_factory)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            first = await client.post("/api/library/sync")
            second = await client.post("/api/library/sync")
            polled = await client.get("/api/library/sync")
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 202
    assert first.json()["state"] == "RUNNING"
    assert len(started) == 1
    assert second.json()["state"] == "RUNNING"
    assert polled.status_code == 200
    assert polled.json()["state"] == "RUNNING"
