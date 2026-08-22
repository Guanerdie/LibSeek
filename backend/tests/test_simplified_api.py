from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import get_viewer_principal
from app.core.auth import Principal
from app.db.session import get_session
from app.main import app
from app.models.enums import AuthRole, MediaType
from app.simple.models import LibraryMediaItem, MediaState


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
        "/api/downloads/sync",
        "/api/configuration",
    }.issubset(paths)

    retired_fragments = (
        "approval-requests",
        "automation",
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
            "country_codes": [],
            "states": [],
            "years": [],
        },
    }


@pytest.mark.asyncio
async def test_library_api_filters_by_nextfind_country_and_common_fields(
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
                    year=2026,
                    state=MediaState.READY,
                ),
                LibraryMediaItem(
                    source_item_id="filter-movie",
                    media_type=MediaType.MOVIE,
                    tmdb_id=200,
                    title="Another Movie",
                    country_codes=["KR"],
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
                    "country_code": "jp",
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
    assert payload["filter_options"] == {
        "media_types": ["movie", "tv"],
        "country_codes": ["JP", "KR", "US"],
        "states": ["NEEDS_ATTENTION", "READY"],
        "years": [2026, 2025],
    }
