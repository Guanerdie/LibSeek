from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import get_viewer_principal
from app.core.auth import Principal
from app.db.session import get_session
from app.main import app
from app.models.enums import AuthRole, MediaType
from app.simple.models import (
    ActivityLog,
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
        response = await client.get("/api/automation/runs", params={"page": 1, "page_size": 1})

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
        "outcomes": {
            "downloaded": 0,
            "existing_download": 0,
            "awaiting_library": 0,
            "no_match": 0,
            "dry_run": 0,
            "skipped": 0,
            "unknown": 17,
        },
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
                        AutomationJobState.FAILED if index == 0 else AutomationJobState.SUCCEEDED
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
    assert payload["items"][0]["outcome"] == "failed"
    assert missing.status_code == 404
    assert missing.json()["error_code"] == "AUTOMATION_RUN_NOT_FOUND"


@pytest.mark.asyncio
async def test_run_outcomes_separate_new_downloads_from_prescreened_items(session_factory) -> None:
    async with session_factory() as session:
        run = AutomationRun(
            id="run-outcomes",
            state=AutomationRunState.SUCCEEDED,
            created_count=8,
            succeeded_count=6,
            failed_count=1,
            deferred_count=1,
            skipped_existing_count=4,
            awaiting_library_count=3,
        )
        session.add(run)
        for index, outcome in enumerate(
            (
                "downloaded",
                "existing_download",
                "awaiting_library",
                "no_match",
                "dry_run",
                "skipped",
                "failed",
                None,
            )
        ):
            media = LibraryMediaItem(
                id=f"outcome-media-{index}",
                source_item_id=f"outcome-source-{index}",
                media_type=MediaType.MOVIE,
                tmdb_id=1000 + index,
                title=f"Outcome {index}",
            )
            session.add(media)
            await session.flush()
            session.add(
                AutomationJob(
                    id=f"outcome-job-{index}",
                    run_id=run.id,
                    media_id=media.id,
                    state=(
                        AutomationJobState.FAILED
                        if outcome == "failed"
                        else (
                            AutomationJobState.RETRY_WAIT
                            if outcome is None
                            else AutomationJobState.SUCCEEDED
                        )
                    ),
                    decision={"outcome": outcome} if outcome else {},
                )
            )
        await session.commit()

    expected = {
        "downloaded": 1,
        "existing_download": 5,
        "awaiting_library": 4,
        "no_match": 1,
        "dry_run": 1,
        "skipped": 1,
        "unknown": 0,
    }
    async for client in _client(session_factory):
        for url in ("/api/automation/runs/run-outcomes", "/api/automation/runs/latest"):
            response = await client.get(url)
            assert response.status_code == 200
            assert response.json()["created"] == 8
            assert response.json()["outcomes"] == expected
        response = await client.get("/api/automation/runs")
        assert response.json()["items"][0]["outcomes"] == expected
        response = await client.get("/api/automation/runs/run-outcomes/jobs")
        assert {item["outcome"] for item in response.json()["items"]} == {
            "downloaded",
            "existing_download",
            "awaiting_library",
            "no_match",
            "dry_run",
            "skipped",
            "failed",
            None,
        }


@pytest.mark.asyncio
async def test_historical_download_success_requires_submission_evidence(session_factory) -> None:
    start = datetime(2026, 10, 20, 1, tzinfo=UTC)
    async with session_factory() as session:
        run = AutomationRun(
            id="run-legacy",
            state=AutomationRunState.SUCCEEDED,
            created_count=4,
            succeeded_count=4,
        )
        session.add(run)
        for index in range(4):
            media = LibraryMediaItem(
                id=f"legacy-media-{index}",
                source_item_id=f"legacy-source-{index}",
                media_type=MediaType.MOVIE,
                tmdb_id=2000 + index,
                title=f"Legacy {index}",
            )
            session.add(media)
            await session.flush()
            search = ReleaseSearch(media_id=media.id, site_ids=["example"])
            session.add(search)
            await session.flush()
            candidate = ReleaseCandidate(
                search_id=search.id,
                site_id="example",
                torrent_id=str(index),
                title=media.title,
                score=0.9,
            )
            session.add(candidate)
            await session.flush()
            download = Download(
                media_id=media.id,
                candidate_id=candidate.id,
                name=media.title,
                state=DownloadState.COMPLETED,
                created_at=start + timedelta(seconds=1) if index < 2 else start - timedelta(days=1),
                submitted_at=start + timedelta(seconds=2) if index == 0 else None,
            )
            session.add(download)
            await session.flush()
            job = AutomationJob(
                id=f"legacy-job-{index}",
                run_id=run.id,
                media_id=media.id,
                state=AutomationJobState.SUCCEEDED,
                selected_candidate_id=candidate.id,
                download_id=download.id,
                created_at=start,
                finished_at=start + timedelta(minutes=1),
                decision={
                    "mode": "live",
                    "download_state": "COMPLETED" if index == 3 else "QUEUED",
                },
            )
            session.add(job)
            # Older qB reuse paths also logged DOWNLOAD_SUBMITTED. The timestamp
            # proving a real write must exist as well (index 1 intentionally lacks it).
            session.add(
                ActivityLog(
                    media_id=media.id,
                    event="DOWNLOAD_SUBMITTED",
                    message="Submitted",
                    details={"job_id": job.id, "download_id": download.id},
                    created_at=start + timedelta(seconds=3),
                )
            )
        await session.commit()

    async for client in _client(session_factory):
        response = await client.get("/api/automation/runs/run-legacy/jobs")
        assert response.status_code == 200
        assert {item["id"]: item["outcome"] for item in response.json()["items"]} == {
            "legacy-job-0": "downloaded",
            "legacy-job-1": "unknown",
            "legacy-job-2": "existing_download",
            "legacy-job-3": "awaiting_library",
        }
        response = await client.get("/api/automation/runs/run-legacy")
        assert response.json()["outcomes"] == {
            "downloaded": 1,
            "existing_download": 1,
            "awaiting_library": 1,
            "no_match": 0,
            "dry_run": 0,
            "skipped": 0,
            "unknown": 1,
        }
