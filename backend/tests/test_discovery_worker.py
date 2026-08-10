from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.base import MediaSourceAdapter
from app.errors import AppError
from app.models.entities import DiscoveryRun, Job, MediaItem
from app.models.enums import IdentityConfidence, JobStatus, MediaType, MetadataStatus
from app.schemas.adapters import (
    AdapterManifest,
    LibraryDetails,
    MediaDiscoveryResult,
    MediaItemData,
    ProbeResult,
)
from app.services.discovery import create_discovery_run
from app.workers.processor import JobProcessor


def media_fixture(title: str = "Test title") -> MediaItemData:
    now = datetime.now(UTC)
    return MediaItemData(
        source="nextfind",
        source_item_id="nextfind:42",
        media_type=MediaType.TV,
        tmdb_id=42,
        title=title,
        year=2026,
        local_episodes=1,
        total_episodes=2,
        aired_episodes=2,
        missing_episodes=["S01E02"],
        identity_confidence=IdentityConfidence.HIGH,
        metadata_status=MetadataStatus.RESOLVED,
        discovered_at=now,
        updated_at=now,
    )


class FakeMediaSource(MediaSourceAdapter):
    def __init__(self, *, title: str = "Test title", error: AppError | None = None) -> None:
        self.title = title
        self.error = error

    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            id="fake",
            name="fake",
            adapter_type="media_source",
            version="1",
            enabled=True,
            mode="MOCK_ONLY",
            description="test",
        )

    async def probe(self) -> ProbeResult:
        return ProbeResult(healthy=True, message="ok")

    async def authenticate(self) -> None:
        if self.error:
            raise self.error

    async def list_missing_media(self) -> MediaDiscoveryResult:
        return MediaDiscoveryResult(items=[media_fixture(self.title)])

    async def get_library_details(
        self, media_type: MediaType, tmdb_id: int
    ) -> LibraryDetails:
        return LibraryDetails(tmdb_id=tmdb_id, media_type=media_type)


@pytest.mark.asyncio
async def test_discovery_creation_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        first, first_dedup = await create_discovery_run(session)
        await session.commit()
        second, second_dedup = await create_discovery_run(session)
        assert first_dedup is False
        assert second_dedup is True
        assert first.id == second.id
        assert await session.scalar(select(func.count()).select_from(Job)) == 1


@pytest.mark.asyncio
async def test_worker_claim_prevents_duplicate_execution(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await create_discovery_run(session)
        await session.commit()
    first = JobProcessor(session_factory, "worker-1", FakeMediaSource)
    second = JobProcessor(session_factory, "worker-2", FakeMediaSource)
    claimed = await first.claim_job()
    assert claimed is not None
    assert await second.claim_job() is None


@pytest.mark.asyncio
async def test_worker_upserts_by_tmdb_and_is_recoverable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        first_run, _ = await create_discovery_run(session)
        await session.commit()
    first = JobProcessor(session_factory, "worker-1", FakeMediaSource)
    assert await first.run_once() is True

    async with session_factory() as session:
        completed = await session.get(DiscoveryRun, first_run.id)
        assert completed is not None and completed.status == JobStatus.SUCCEEDED
        assert completed.created_count == 1
        second_run, _ = await create_discovery_run(session)
        await session.commit()

    second = JobProcessor(
        session_factory, "worker-2", lambda: FakeMediaSource(title="Updated title")
    )
    assert await second.run_once() is True
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(MediaItem)) == 1
        item = await session.scalar(select(MediaItem))
        assert item is not None and item.title == "Updated title"
        run = await session.get(DiscoveryRun, second_run.id)
        assert run is not None and run.updated_count == 1


@pytest.mark.asyncio
async def test_retryable_failure_enters_retry_wait(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        run, _ = await create_discovery_run(session)
        await session.commit()
    error = AppError("UPSTREAM_TIMEOUT", "上游超时", retryable=True)
    processor = JobProcessor(
        session_factory, "worker-1", lambda: FakeMediaSource(error=error)
    )
    assert await processor.run_once() is True
    async with session_factory() as session:
        job = await session.scalar(select(Job))
        refreshed = await session.get(DiscoveryRun, run.id)
        assert job is not None and job.status == JobStatus.RETRY_WAIT
        assert job.next_retry_at is not None
        assert refreshed is not None and refreshed.status == JobStatus.RETRY_WAIT


@pytest.mark.asyncio
async def test_worker_enriches_unknown_episode_fields_from_read_only_details() -> None:
    item = media_fixture().model_copy(
        update={
            "local_episodes": None,
            "total_episodes": None,
            "aired_episodes": None,
            "missing_episodes": None,
        }
    )
    adapter = FakeMediaSource()

    async def details(media_type: MediaType, tmdb_id: int) -> LibraryDetails:
        return LibraryDetails(
            tmdb_id=tmdb_id,
            media_type=media_type,
            local_episodes=3,
            total_episodes=8,
            aired_episodes=6,
            missing_episodes=["S01E04"],
        )

    adapter.get_library_details = details  # type: ignore[method-assign]
    enriched, warnings = await JobProcessor._enrich_library_details(adapter, [item], [])
    assert not warnings
    assert enriched[0].local_episodes == 3
    assert enriched[0].missing_episodes == ["S01E04"]
