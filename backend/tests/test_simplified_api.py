from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import get_operator_principal, get_viewer_principal
from app.core.auth import Principal
from app.db.session import get_session
from app.main import app
from app.models.enums import AuthRole, MediaType
from app.simple import routes as simple_routes
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


def test_runtime_exposes_the_daily_flow_without_retired_control_planes() -> None:
    paths = set(app.openapi()["paths"])

    assert {
        "/api/health",
        "/api/library",
        "/api/library/sync",
        "/api/library/{media_id}",
        "/api/library/{media_id}/identify",
        "/api/library/{media_id}/searches",
        "/api/searches/{search_id}",
        "/api/candidates/{candidate_id}/download",
        "/api/downloads",
        "/api/downloads/{download_id}/retry",
        "/api/downloads/sync",
        "/api/configuration",
        "/api/automation/policy",
        "/api/automation/jobs",
        "/api/automation/runs",
        "/api/automation/jobs/{job_id}/retry",
    }.issubset(paths)

    retired_fragments = (
        "approval-requests",
        "download-batches",
        "download-executions",
        "download-jobs",
        "execution-intents",
        "media-import",
        "torrent-searches",
    )
    assert not any(fragment in path for fragment in retired_fragments for path in paths)


@pytest.mark.asyncio
async def test_library_api_reads_the_simplified_database(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async def session_override() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    async def viewer_override() -> Principal:
        return Principal(
            username="viewer",
            role=AuthRole.VIEWER,
            issued_at=0,
            expires_at=2_000_000_000,
            csrf_digest="test",
        )

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_viewer_principal] = viewer_override
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get("/api/library")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 30,
        "filter_options": {
            "media_types": [],
            "regions": ["欧美", "大陆", "港台", "韩国", "日本", "亚太"],
            "states": [],
            "years": [],
        },
    }


@pytest.mark.asyncio
async def test_library_api_filters_by_nextfind_region_and_common_fields(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add_all(
            [
                LibraryMediaItem(
                    source_item_id="filter-tv",
                    media_type=MediaType.TV,
                    tmdb_id=100,
                    title="A Missing Show",
                    country_codes=["JP", "US"],
                    original_language="ja",
                    year=2026,
                    state=MediaState.READY,
                ),
                LibraryMediaItem(
                    source_item_id="filter-movie",
                    media_type=MediaType.MOVIE,
                    tmdb_id=200,
                    title="Another Movie",
                    country_codes=["KR"],
                    original_language="ko",
                    year=2025,
                    state=MediaState.NEEDS_ATTENTION,
                ),
            ]
        )
        await session.commit()

    async def session_override() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    async def viewer_override() -> Principal:
        return Principal(
            username="viewer",
            role=AuthRole.VIEWER,
            issued_at=0,
            expires_at=2_000_000_000,
            csrf_digest="test",
        )

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_viewer_principal] = viewer_override
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get(
                "/api/library",
                params={
                    "media_type": "tv",
                    "region": "日本",
                    "year": 2026,
                    "query": "missing",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert [item["source_item_id"] for item in payload["items"]] == ["filter-tv"]
    assert payload["items"][0]["country_codes"] == ["JP", "US"]
    assert payload["items"][0]["original_language"] == "ja"
    assert payload["items"][0]["regions"] == ["欧美", "日本"]
    assert payload["filter_options"] == {
        "media_types": ["movie", "tv"],
        "regions": ["欧美", "大陆", "港台", "韩国", "日本", "亚太"],
        "states": ["NEEDS_ATTENTION", "READY"],
        "years": [2026, 2025],
    }


@pytest.mark.asyncio
async def test_media_detail_returns_latest_failed_search_with_error_message(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="latest-search-media",
            media_type=MediaType.TV,
            tmdb_id=300,
            title="Latest Search Show",
            state=MediaState.NEEDS_ATTENTION,
        )
        session.add(media)
        await session.flush()
        older_search = ReleaseSearch(
            media_id=media.id,
            site_ids=["avistaz"],
            state=SearchState.SUCCEEDED,
            created_at=datetime(2026, 8, 21, 12, tzinfo=UTC),
            finished_at=datetime(2026, 8, 21, 12, 1, tzinfo=UTC),
        )
        latest_search = ReleaseSearch(
            media_id=media.id,
            site_ids=["avistaz"],
            state=SearchState.FAILED,
            error_message="PT 站点暂时不可用",
            created_at=datetime(2026, 8, 22, 12, tzinfo=UTC),
            finished_at=datetime(2026, 8, 22, 12, 1, tzinfo=UTC),
        )
        session.add_all([older_search, latest_search])
        await session.commit()
        media_id = media.id
        latest_search_id = latest_search.id

    async def session_override() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    async def viewer_override() -> Principal:
        return Principal(
            username="viewer",
            role=AuthRole.VIEWER,
            issued_at=0,
            expires_at=2_000_000_000,
            csrf_digest="test",
        )

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_viewer_principal] = viewer_override
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get(f"/api/library/{media_id}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["latest_search"] == {
        "id": latest_search_id,
        "media_id": media_id,
        "site_ids": ["avistaz"],
        "state": "FAILED",
        "error_message": "PT 站点暂时不可用",
        "created_at": "2026-08-22T12:00:00",
        "finished_at": "2026-08-22T12:01:00",
    }


@pytest.mark.asyncio
async def test_search_api_exposes_the_public_pt_details_page(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="candidate-details-link",
            media_type=MediaType.TV,
            tmdb_id=300,
            title="Linked Show",
            state=MediaState.CANDIDATES,
        )
        session.add(media)
        await session.flush()
        search = ReleaseSearch(
            media_id=media.id,
            site_ids=["avistaz"],
            state=SearchState.SUCCEEDED,
        )
        session.add(search)
        await session.flush()
        session.add(
            ReleaseCandidate(
                search_id=search.id,
                site_id="avistaz",
                torrent_id="114307",
                title="Linked.Show.1080p.WEB-DL",
                download_factor=0,
                score=0.9,
                reasons=[],
                warnings=[],
            )
        )
        await session.commit()
        search_id = search.id

    async def session_override() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    async def viewer_override() -> Principal:
        return Principal(
            username="viewer",
            role=AuthRole.VIEWER,
            issued_at=0,
            expires_at=2_000_000_000,
            csrf_digest="test",
        )

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_viewer_principal] = viewer_override
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get(f"/api/searches/{search_id}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["candidates"][0]["details_url"] == (
        "https://avistaz.to/torrents/114307"
    )
    assert response.json()["candidates"][0]["download_factor"] == 0


@pytest.mark.asyncio
async def test_automation_policy_api_returns_safe_defaults(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async def session_override() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    async def viewer_override() -> Principal:
        return Principal(
            username="viewer",
            role=AuthRole.VIEWER,
            issued_at=0,
            expires_at=2_000_000_000,
            csrf_digest="test",
        )

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_viewer_principal] = viewer_override
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get("/api/automation/policy")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is False
    assert payload["dry_run"] is True
    assert payload["interval_minutes"] == 60
    assert payload["retry_delay_minutes"] == 30
    assert payload["daily_download_limit"] == 3
    assert payload["last_run_at"] is None


@pytest.mark.asyncio
async def test_failed_automation_job_can_be_explicitly_retried(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="api-automation-retry",
            media_type=MediaType.MOVIE,
            tmdb_id=700,
            title="Retry from API",
            state=MediaState.NEEDS_ATTENTION,
        )
        session.add(media)
        await session.flush()
        job = AutomationJob(
            run_id="api-run",
            media_id=media.id,
            state=AutomationJobState.FAILED,
            attempt_count=3,
            error_message="PT 配置错误",
        )
        session.add(job)
        await session.commit()
        job_id = job.id

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
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.post(f"/api/automation/jobs/{job_id}/retry")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["media_title"] == "Retry from API"
    assert payload["state"] == "RETRY_WAIT"
    assert payload["attempt_count"] == 0
    assert payload["next_attempt_at"] is not None


@pytest.mark.asyncio
async def test_failed_download_can_be_retried_through_the_api(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="api-download-retry",
            media_type=MediaType.MOVIE,
            tmdb_id=701,
            title="Retry download from API",
            state=MediaState.NEEDS_ATTENTION,
        )
        session.add(media)
        await session.flush()
        search = ReleaseSearch(
            media_id=media.id,
            site_ids=["avistaz"],
            state=SearchState.SUCCEEDED,
        )
        session.add(search)
        await session.flush()
        candidate = ReleaseCandidate(
            search_id=search.id,
            site_id="avistaz",
            torrent_id="api-download-retry",
            title="Retry.Movie.1080p.WEB-DL",
            score=0.9,
            reasons=[],
            warnings=[],
        )
        session.add(candidate)
        await session.flush()
        download = Download(
            media_id=media.id,
            candidate_id=candidate.id,
            name=candidate.title,
            state=DownloadState.ERROR,
            error_message="PT 站点暂时不可用",
        )
        session.add(download)
        await session.commit()
        download_id = download.id

    async def fake_retry_download(
        session: AsyncSession,
        *,
        download_id: str,
        **_kwargs: object,
    ) -> Download:
        row = await session.get(Download, download_id)
        assert row is not None
        row.state = DownloadState.QUEUED
        row.error_message = None
        await session.commit()
        await session.refresh(row)
        return row

    monkeypatch.setattr(simple_routes, "retry_download", fake_retry_download)

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
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.post(f"/api/downloads/{download_id}/retry")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["id"] == download_id
    assert response.json()["state"] == "QUEUED"
    assert response.json()["error_message"] is None
