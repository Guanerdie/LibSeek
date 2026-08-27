from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import get_viewer_principal
from app.core.auth import Principal
from app.db.session import get_session
from app.main import app
from app.models.enums import AuthRole, MediaType
from app.simple.models import (
    AutomationJob,
    AutomationJobState,
    AutomationRun,
    AutomationRunState,
    LibraryMediaItem,
    MediaState,
)


def _viewer() -> Principal:
    return Principal(
        username="viewer",
        role=AuthRole.VIEWER,
        issued_at=0,
        expires_at=2_000_000_000,
        csrf_digest="test",
    )


async def _client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    async def session_override() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    async def viewer_override() -> Principal:
        return _viewer()

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_viewer_principal] = viewer_override
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_automation_runs_api_returns_paginated_execution_summaries(session_factory) -> None:
    async with session_factory() as session:
        session.add_all(
            [
                AutomationRun(
                    id="run-old",
                    trigger="scheduled",
                    state=AutomationRunState.SUCCEEDED,
                    created_count=20,
                    succeeded_count=20,
                    created_at=datetime(2026, 8, 26, 1, tzinfo=UTC),
                    finished_at=datetime(2026, 8, 26, 2, tzinfo=UTC),
                ),
                AutomationRun(
                    id="run-new",
                    trigger="manual",
                    state=AutomationRunState.FAILED,
                    created_count=20,
                    succeeded_count=17,
                    failed_count=3,
                    created_at=datetime(2026, 8, 27, 1, tzinfo=UTC),
                    finished_at=datetime(2026, 8, 27, 2, tzinfo=UTC),
                    error_message="搜索服务暂时不可用",
                ),
            ]
        )
        await session.commit()

    async for client in _client(session_factory):
        response = await client.get(
            "/api/automation/runs", params={"page": 1, "page_size": 1}
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["page"] == 1
    assert payload["page_size"] == 1
    assert len(payload["items"]) == 1
    assert payload["items"][0] == {
        "id": "run-new",
        "trigger": "manual",
        "state": "FAILED",
        "created": 20,
        "succeeded": 17,
        "failed": 3,
        "deferred": 0,
        "error_message": "搜索服务暂时不可用",
        "created_at": "2026-08-27T01:00:00",
        "started_at": None,
        "finished_at": "2026-08-27T02:00:00",
    }


@pytest.mark.asyncio
async def test_automation_run_jobs_api_scopes_and_paginates_tasks(session_factory) -> None:
    async with session_factory() as session:
        run = AutomationRun(
            id="run-detail",
            state=AutomationRunState.SUCCEEDED,
            created_count=3,
            succeeded_count=2,
            failed_count=1,
            created_at=datetime(2026, 8, 27, 3, tzinfo=UTC),
        )
        media_items = [
            LibraryMediaItem(
                id=f"media-{index}",
                source_item_id=f"run-detail-{index}",
                media_type=MediaType.MOVIE,
                tmdb_id=900 + index,
                title=f"任务影视 {index}",
                state=MediaState.NEEDS_ATTENTION,
            )
            for index in range(3)
        ]
        session.add(run)
        session.add_all(media_items)
        await session.flush()
        session.add_all(
            [
                AutomationJob(
                    id=f"job-{index}",
                    run_id=run.id,
                    media_id=media.id,
                    state=(
                        AutomationJobState.FAILED
                        if index == 0
                        else AutomationJobState.SUCCEEDED
                    ),
                    error_message="搜索失败" if index == 0 else None,
                    created_at=datetime(2026, 8, 27, 3, index, tzinfo=UTC),
                )
                for index, media in enumerate(media_items)
            ]
        )
        # A job from another run must never leak into this detail view.
        other_media = LibraryMediaItem(
            id="media-other",
            source_item_id="run-other-media",
            media_type=MediaType.MOVIE,
            tmdb_id=999,
            title="其他运行",
            state=MediaState.NEEDS_ATTENTION,
        )
        session.add(other_media)
        await session.flush()
        session.add(
            AutomationJob(
                id="job-other",
                run_id="run-other",
                media_id=other_media.id,
                state=AutomationJobState.SUCCEEDED,
            )
        )
        await session.commit()

    async for client in _client(session_factory):
        response = await client.get(
            f"/api/automation/runs/{run.id}/jobs",
            params={"page": 2, "page_size": 2},
        )
        missing = await client.get("/api/automation/runs/does-not-exist/jobs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["page"] == 2
    assert payload["page_size"] == 2
    assert [item["id"] for item in payload["items"]] == ["job-0"]
    assert payload["items"][0]["media_title"] == "任务影视 0"
    assert payload["items"][0]["error_message"] == "搜索失败"
    assert missing.status_code == 404
    assert missing.json()["error_code"] == "AUTOMATION_RUN_NOT_FOUND"

