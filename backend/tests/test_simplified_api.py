from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import get_viewer_principal
from app.core.auth import Principal
from app.db.session import get_session
from app.main import app
from app.models.enums import AuthRole


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
    assert response.json() == {"items": [], "total": 0, "page": 1, "page_size": 30}
