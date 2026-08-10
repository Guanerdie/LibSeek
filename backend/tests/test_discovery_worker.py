from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
        local_episode_matrix={1: [1]},
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
        assert item.local_episode_matrix == {"1": [1]}
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
            "local_episode_matrix": None,
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
            local_episode_matrix={1: [1, 2, 3]},
            total_episodes=8,
            aired_episodes=6,
            missing_episodes=["S01E04"],
        )

    adapter.get_library_details = details  # type: ignore[method-assign]
    enriched, warnings = await JobProcessor._enrich_library_details(adapter, [item], [])
    assert not warnings
    assert enriched[0].local_episodes == 3
    assert enriched[0].local_episode_matrix == {1: [1, 2, 3]}
    assert enriched[0].missing_episodes == ["S01E04"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_type", ("pydantic", "value_error"))
async def test_worker_isolates_invalid_single_library_detail_and_continues(
    failure_type: str,
) -> None:
    first = media_fixture().model_copy(
        update={
            "local_episodes": None,
            "local_episode_matrix": None,
            "total_episodes": None,
            "aired_episodes": None,
            "missing_episodes": None,
        }
    )
    second = first.model_copy(
        update={"source_item_id": "nextfind:43", "tmdb_id": 43, "title": "Second"}
    )
    adapter = FakeMediaSource()

    async def details(media_type: MediaType, tmdb_id: int) -> LibraryDetails:
        if tmdb_id == 42:
            if failure_type == "value_error":
                raise ValueError("invalid local details")
            return LibraryDetails.model_validate(
                {
                    "tmdb_id": tmdb_id,
                    "media_type": media_type,
                    "local_episode_matrix": {"invalid-season": [1]},
                }
            )
        return LibraryDetails(
            tmdb_id=tmdb_id,
            media_type=media_type,
            local_episode_matrix={1: [1, 2]},
        )

    adapter.get_library_details = details  # type: ignore[method-assign]
    enriched, warnings = await JobProcessor._enrich_library_details(
        adapter, [first, second], []
    )

    assert len(enriched) == 2
    assert enriched[0].local_episode_matrix is None
    assert enriched[1].local_episode_matrix == {1: [1, 2]}
    assert [warning.error_code for warning in warnings] == ["LIBRARY_DETAILS_ISOLATED"]


@pytest.mark.asyncio
async def test_worker_renews_only_the_current_lease(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await create_discovery_run(session)
        await session.commit()
    processor = JobProcessor(session_factory, "worker-1", FakeMediaSource)
    claimed = await processor.claim_job()
    assert claimed is not None and claimed.lease_token is not None
    original_locked_at = claimed.locked_at

    assert await processor.renew_lease(claimed.id, "wrong-token") is False
    assert await processor.renew_lease(claimed.id, claimed.lease_token) is True

    async with session_factory() as session:
        refreshed = await session.get(Job, claimed.id)
        assert refreshed is not None
        assert refreshed.locked_at is not None
        assert original_locked_at is not None
        assert refreshed.locked_at.replace(tzinfo=UTC) >= original_locked_at


@pytest.mark.asyncio
async def test_stale_worker_cannot_commit_after_job_is_reclaimed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        run, _ = await create_discovery_run(session)
        await session.commit()
    first = JobProcessor(
        session_factory,
        "worker-1",
        FakeMediaSource,
        lease_seconds=1,
        lease_renew_interval_seconds=0.5,
    )
    stale_claim = await first.claim_job()
    assert stale_claim is not None and stale_claim.lease_token is not None
    async with session_factory() as session:
        job = await session.get(Job, stale_claim.id)
        assert job is not None
        job.locked_at = datetime.now(UTC) - timedelta(seconds=2)
        await session.commit()

    second = JobProcessor(
        session_factory,
        "worker-2",
        FakeMediaSource,
        lease_seconds=1,
        lease_renew_interval_seconds=0.5,
    )
    current_claim = await second.claim_job()
    assert current_claim is not None
    await first._complete_discovery(
        stale_claim.id,
        run.id,
        [media_fixture()],
        [],
        lease_token=stale_claim.lease_token,
    )

    async with session_factory() as session:
        job = await session.get(Job, stale_claim.id)
        refreshed_run = await session.get(DiscoveryRun, run.id)
        assert job is not None and job.status == JobStatus.RUNNING
        assert job.locked_by == "worker-2"
        assert refreshed_run is not None and refreshed_run.status == JobStatus.RUNNING
        assert await session.scalar(select(func.count()).select_from(MediaItem)) == 0
