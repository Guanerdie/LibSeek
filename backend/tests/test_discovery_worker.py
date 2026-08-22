from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.base import MediaSourceAdapter
from app.adapters.metadata.mock_tmdb import MockTmdbProvider
from app.core.config import Settings
from app.errors import AppError
from app.models.entities import DiscoveryRun, Job, MediaItem, TorrentSearchRun
from app.models.enums import (
    IdentityConfidence,
    JobStatus,
    MediaType,
    MetadataStatus,
    WorkflowStatus,
)
from app.schemas.adapters import (
    AdapterManifest,
    LibraryDetails,
    MediaDiscoveryResult,
    MediaItemData,
    MetadataRecord,
    ProbeResult,
)
from app.services.discovery import create_discovery_run
from app.workers.main import build_processor
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


def metadata_fixture(*, country_codes: list[str] | None) -> MetadataRecord:
    return MetadataRecord(
        tmdb_id=42,
        media_type=MediaType.TV,
        title="Test title",
        country_codes=country_codes,
        confidence=1,
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


class FixedMediaSource(FakeMediaSource):
    def __init__(self, item: MediaItemData) -> None:
        super().__init__()
        self.item = item

    async def list_missing_media(self) -> MediaDiscoveryResult:
        return MediaDiscoveryResult(items=[self.item])


@pytest.mark.parametrize(
    ("enable_tmdb_live", "access_token", "expected"),
    ((True, "test-token", True), (False, "test-token", False), (True, None, False)),
)
def test_worker_auto_enqueue_requires_ready_tmdb_configuration(
    enable_tmdb_live: bool,
    access_token: str | None,
    expected: bool,
) -> None:
    settings = Settings(
        _env_file=None,
        enable_tmdb_live=enable_tmdb_live,
        tmdb_access_token=access_token,
    )

    processor = build_processor(settings, "worker-tmdb-readiness")

    assert processor.auto_enqueue_metadata_resolution is expected


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
async def test_successful_discovery_archives_items_no_longer_reported_and_restores_them(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    stale = MediaItem(
        source="nextfind",
        source_item_id="nextfind:43",
        media_type=MediaType.TV,
        tmdb_id=43,
        title="Now in library",
        discovery_status="MISSING",
        identity_confidence=IdentityConfidence.HIGH,
        metadata_status=MetadataStatus.RESOLVED,
        discovered_at=now,
        updated_at=now,
    )
    async with session_factory() as session:
        session.add(stale)
        first_run, _ = await create_discovery_run(session)
        await session.commit()

    processor = JobProcessor(session_factory, "worker-reconcile", FakeMediaSource)
    assert await processor.run_once() is True

    async with session_factory() as session:
        archived = await session.get(MediaItem, stale.id)
        run = await session.get(DiscoveryRun, first_run.id)
        assert archived is not None and archived.discovery_status == "IN_LIBRARY"
        assert run is not None
        second_run, _ = await create_discovery_run(session)
        await session.commit()

    restored_item = media_fixture("Returned").model_copy(
        update={"source_item_id": "nextfind:43", "tmdb_id": 43}
    )
    restored_processor = JobProcessor(
        session_factory,
        "worker-restore",
        lambda: FixedMediaSource(restored_item),
    )
    assert await restored_processor.run_once() is True

    async with session_factory() as session:
        restored = await session.get(MediaItem, stale.id)
        assert restored is not None and restored.discovery_status == "MISSING"
        assert restored.title == "Returned"
        assert await session.get(DiscoveryRun, second_run.id) is not None


@pytest.mark.asyncio
async def test_failed_discovery_does_not_archive_existing_missing_items(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    existing = MediaItem(
        source="nextfind",
        source_item_id="nextfind:44",
        media_type=MediaType.TV,
        tmdb_id=44,
        title="Keep on failure",
        discovery_status="MISSING",
        identity_confidence=IdentityConfidence.HIGH,
        metadata_status=MetadataStatus.RESOLVED,
        discovered_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    async with session_factory() as session:
        session.add(existing)
        await create_discovery_run(session)
        await session.commit()

    processor = JobProcessor(
        session_factory,
        "worker-reconcile-failure",
        lambda: FakeMediaSource(error=AppError("NEXTFIND_DOWN", "down")),
    )
    assert await processor.run_once() is True

    async with session_factory() as session:
        preserved = await session.get(MediaItem, existing.id)
        assert preserved is not None and preserved.discovery_status == "MISSING"


@pytest.mark.asyncio
async def test_discovery_enqueues_new_missing_media_without_calling_tmdb(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    item = media_fixture().model_copy(update={"country_codes": []})
    async with session_factory() as session:
        await create_discovery_run(session)
        await session.commit()

    def forbidden_provider() -> MockTmdbProvider:
        raise AssertionError("discovery enqueue must not instantiate the TMDB provider")

    processor = JobProcessor(
        session_factory,
        "worker-auto-resolve-new",
        lambda: FixedMediaSource(item),
        forbidden_provider,
        auto_enqueue_metadata_resolution=True,
    )

    assert await processor.run_once() is True

    async with session_factory() as session:
        media = await session.scalar(select(MediaItem))
        resolution_jobs = list(
            (
                await session.scalars(
                    select(Job).where(Job.job_type.like("RESOLVE_METADATA:%"))
                )
            ).all()
        )
        assert media is not None
        assert media.discovery_status == "MISSING"
        assert media.workflow_status == WorkflowStatus.METADATA_PENDING
        assert len(resolution_jobs) == 1
        assert resolution_jobs[0].status == JobStatus.PENDING
        assert resolution_jobs[0].payload["media_id"] == media.id
        assert resolution_jobs[0].payload["read_only"] is True


@pytest.mark.asyncio
async def test_discovery_does_not_enqueue_metadata_when_tmdb_is_not_ready(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    item = media_fixture().model_copy(update={"country_codes": []})
    async with session_factory() as session:
        await create_discovery_run(session)
        await session.commit()

    processor = JobProcessor(
        session_factory,
        "worker-auto-resolve-disabled",
        lambda: FixedMediaSource(item),
    )

    assert await processor.run_once() is True

    async with session_factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Job)
                .where(Job.job_type.like("RESOLVE_METADATA:%"))
            )
            == 0
        )


@pytest.mark.asyncio
async def test_discovery_does_not_requeue_when_only_non_identity_input_changes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first_item = media_fixture().model_copy(update={"country_codes": []})
    async with session_factory() as session:
        await create_discovery_run(session)
        await session.commit()
    first = JobProcessor(
        session_factory,
        "worker-auto-resolve-first",
        lambda: FixedMediaSource(first_item),
        auto_enqueue_metadata_resolution=True,
    )
    assert await first.run_once() is True

    async with session_factory() as session:
        first_resolution = await session.scalar(
            select(Job).where(Job.job_type.like("RESOLVE_METADATA:%"))
        )
        assert first_resolution is not None
        first_resolution.status = JobStatus.SUCCEEDED
        await create_discovery_run(session)
        await session.commit()

    refreshed_item = first_item.model_copy(
        update={
            "local_episodes": 2,
            "local_episode_matrix": {1: [1, 2]},
            "missing_episodes": [],
            "updated_at": datetime.now(UTC) + timedelta(seconds=1),
        }
    )
    second = JobProcessor(
        session_factory,
        "worker-auto-resolve-unchanged",
        lambda: FixedMediaSource(refreshed_item),
        auto_enqueue_metadata_resolution=True,
    )
    assert await second.run_once() is True

    async with session_factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Job)
                .where(Job.job_type.like("RESOLVE_METADATA:%"))
            )
            == 1
        )


@pytest.mark.asyncio
async def test_discovery_replaces_active_resolution_when_identity_input_changes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first_item = media_fixture().model_copy(update={"country_codes": []})
    async with session_factory() as session:
        await create_discovery_run(session)
        await session.commit()
    first = JobProcessor(
        session_factory,
        "worker-auto-resolve-stale-first",
        lambda: FixedMediaSource(first_item),
        auto_enqueue_metadata_resolution=True,
    )
    assert await first.run_once() is True

    async with session_factory() as session:
        stale_resolution = await session.scalar(
            select(Job).where(Job.job_type.like("RESOLVE_METADATA:%"))
        )
        assert stale_resolution is not None
        stale_resolution_id = stale_resolution.id
        second_run, _ = await create_discovery_run(session)
        discovery_job = await session.scalar(select(Job).where(Job.run_id == second_run.id))
        assert discovery_job is not None
        discovery_job.status = JobStatus.RUNNING
        discovery_job.locked_by = "worker-auto-resolve-stale-second"
        discovery_job.locked_at = datetime.now(UTC)
        discovery_job.lease_token = "discovery-input-changed"
        await session.commit()

    changed_item = first_item.model_copy(
        update={"title": "Changed identity title", "updated_at": datetime.now(UTC)}
    )
    second = JobProcessor(
        session_factory,
        "worker-auto-resolve-stale-second",
        lambda: FixedMediaSource(changed_item),
        auto_enqueue_metadata_resolution=True,
    )
    await second._complete_discovery(
        discovery_job.id,
        second_run.id,
        [changed_item],
        [],
        lease_token="discovery-input-changed",
    )

    async with session_factory() as session:
        resolution_jobs = list(
            (
                await session.scalars(
                    select(Job)
                    .where(Job.job_type.like("RESOLVE_METADATA:%"))
                    .order_by(Job.created_at, Job.id)
                )
            ).all()
        )
        assert len(resolution_jobs) == 2
        stale = next(job for job in resolution_jobs if job.id == stale_resolution_id)
        current = next(job for job in resolution_jobs if job.id != stale_resolution_id)
        assert stale.status == JobStatus.CANCELLED
        assert current.status == JobStatus.PENDING
        media = await session.scalar(select(MediaItem))
        assert media is not None and media.title == "Changed identity title"


@pytest.mark.asyncio
async def test_discovery_restores_and_requeues_media_reported_missing_again(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    existing = MediaItem(
        source="nextfind",
        source_item_id="nextfind:42",
        media_type=MediaType.TV,
        tmdb_id=42,
        title="Existing title",
        country_codes=[],
        discovery_status="IN_LIBRARY",
        identity_confidence=IdentityConfidence.HIGH,
        metadata_status=MetadataStatus.RESOLVED,
        discovered_at=now,
        updated_at=now,
    )
    async with session_factory() as session:
        session.add(existing)
        await create_discovery_run(session)
        await session.commit()

    changed_item = media_fixture("Changed title").model_copy(update={"country_codes": []})
    processor = JobProcessor(
        session_factory,
        "worker-auto-resolve-not-missing",
        lambda: FixedMediaSource(changed_item),
        auto_enqueue_metadata_resolution=True,
    )

    assert await processor.run_once() is True

    async with session_factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Job)
                .where(Job.job_type.like("RESOLVE_METADATA:%"))
            )
            == 1
        )
        restored = await session.get(MediaItem, existing.id)
        assert restored is not None and restored.discovery_status == "MISSING"


@pytest.mark.asyncio
@pytest.mark.parametrize("finalization_evidence", ("confirmed", "search_history"))
async def test_discovery_skips_finalized_identity_without_failing(
    session_factory: async_sessionmaker[AsyncSession],
    finalization_evidence: str,
) -> None:
    now = datetime.now(UTC)
    existing = MediaItem(
        source="nextfind",
        source_item_id="nextfind:42",
        media_type=MediaType.TV,
        tmdb_id=42,
        title="Confirmed title",
        country_codes=[],
        discovery_status="MISSING",
        identity_confidence=IdentityConfidence.HIGH,
        metadata_status=MetadataStatus.RESOLVED,
        workflow_status=(
            WorkflowStatus.IDENTITY_CONFIRMED
            if finalization_evidence == "confirmed"
            else WorkflowStatus.NO_CANDIDATE
        ),
        discovered_at=now,
        updated_at=now,
    )
    async with session_factory() as session:
        session.add(existing)
        await session.flush()
        if finalization_evidence == "search_history":
            session.add(
                TorrentSearchRun(
                    media_id=existing.id,
                    site_id="avistaz",
                    status=WorkflowStatus.NO_CANDIDATE,
                )
            )
        run, _ = await create_discovery_run(session)
        await session.commit()

    changed_item = media_fixture("Changed after finalization").model_copy(
        update={"country_codes": []}
    )
    processor = JobProcessor(
        session_factory,
        f"worker-finalized-{finalization_evidence}",
        lambda: FixedMediaSource(changed_item),
        auto_enqueue_metadata_resolution=True,
    )

    assert await processor.run_once() is True

    async with session_factory() as session:
        completed = await session.get(DiscoveryRun, run.id)
        assert completed is not None and completed.status == JobStatus.SUCCEEDED
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Job)
                .where(Job.job_type.like("RESOLVE_METADATA:%"))
            )
            == 0
        )


@pytest.mark.asyncio
async def test_worker_enriches_missing_country_codes_and_persists_them(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await create_discovery_run(session)
        await session.commit()
    provider = MockTmdbProvider([metadata_fixture(country_codes=["JP", "US"])])
    processor = JobProcessor(
        session_factory,
        "worker-country",
        FakeMediaSource,
        lambda: provider,
    )

    assert await processor.run_once() is True

    async with session_factory() as session:
        item = await session.scalar(select(MediaItem))
        assert item is not None
        assert item.country_codes == ["JP", "US"]


@pytest.mark.asyncio
async def test_country_enrichment_keeps_source_values_and_skips_unknown_identity() -> None:
    source_known = media_fixture().model_copy(update={"country_codes": ["KR"]})
    identity_unknown = media_fixture().model_copy(
        update={"source_item_id": "nextfind:temporary", "tmdb_id": None}
    )

    class FailingProvider(MockTmdbProvider):
        async def get_country_codes(self, media_type: MediaType, tmdb_id: int) -> list[str] | None:
            raise AssertionError("TMDB must not be called")

    processor = JobProcessor(
        None,  # type: ignore[arg-type]
        "worker-country-skip",
        FakeMediaSource,
        FailingProvider,
    )
    enriched, warnings = await processor._enrich_country_codes(
        [source_known, identity_unknown], []
    )

    assert not warnings
    assert enriched[0].country_codes == ["KR"]
    assert enriched[1].country_codes is None


@pytest.mark.asyncio
async def test_country_enrichment_deduplicates_by_media_type_and_tmdb_id() -> None:
    items = [
        media_fixture(),
        media_fixture().model_copy(update={"source_item_id": "nextfind:duplicate"}),
        media_fixture().model_copy(
            update={"source_item_id": "nextfind:movie-42", "media_type": MediaType.MOVIE}
        ),
        media_fixture().model_copy(
            update={"source_item_id": "nextfind:43", "tmdb_id": 43}
        ),
    ]

    class RecordingProvider(MockTmdbProvider):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[tuple[MediaType, int]] = []

        async def get_country_codes(
            self, media_type: MediaType, tmdb_id: int
        ) -> list[str] | None:
            self.calls.append((media_type, tmdb_id))
            return {
                (MediaType.TV, 42): ["JP"],
                (MediaType.MOVIE, 42): ["US"],
                (MediaType.TV, 43): ["KR"],
            }[(media_type, tmdb_id)]

    provider = RecordingProvider()
    processor = JobProcessor(
        None,  # type: ignore[arg-type]
        "worker-country-deduplication",
        FakeMediaSource,
        lambda: provider,
    )

    enriched, warnings = await processor._enrich_country_codes(items, [])

    assert not warnings
    assert provider.calls == [
        (MediaType.TV, 42),
        (MediaType.MOVIE, 42),
        (MediaType.TV, 43),
    ]
    assert [item.country_codes for item in enriched] == [
        ["JP"],
        ["JP"],
        ["US"],
        ["KR"],
    ]


@pytest.mark.asyncio
async def test_country_enrichment_checkpoints_completed_batches() -> None:
    items = [
        media_fixture(),
        media_fixture().model_copy(
            update={"source_item_id": "nextfind:43", "tmdb_id": 43}
        ),
        media_fixture().model_copy(
            update={"source_item_id": "nextfind:44", "tmdb_id": 44}
        ),
    ]

    class CountryProvider(MockTmdbProvider):
        async def get_country_codes(
            self, media_type: MediaType, tmdb_id: int
        ) -> list[str] | None:
            del media_type
            return ["JP"] if tmdb_id != 43 else None

    checkpoints: list[dict[tuple[MediaType, int], list[str]]] = []

    async def checkpoint(values: dict[tuple[MediaType, int], list[str]]) -> None:
        checkpoints.append(dict(values))

    processor = JobProcessor(
        None,  # type: ignore[arg-type]
        "worker-country-checkpoint-batches",
        FakeMediaSource,
        CountryProvider,
        country_checkpoint_batch_size=2,
    )

    enriched, warnings = await processor._enrich_country_codes(
        items, [], checkpoint=checkpoint
    )

    assert not warnings
    assert checkpoints == [
        {(MediaType.TV, 42): ["JP"]},
        {(MediaType.TV, 44): ["JP"]},
    ]
    assert [item.country_codes for item in enriched] == [["JP"], None, ["JP"]]


@pytest.mark.asyncio
async def test_country_enrichment_isolates_not_found_once_and_continues() -> None:
    items = [
        media_fixture(),
        media_fixture().model_copy(update={"source_item_id": "nextfind:duplicate"}),
        media_fixture().model_copy(
            update={"source_item_id": "nextfind:43", "tmdb_id": 43}
        ),
    ]

    class PartiallyMissingProvider(MockTmdbProvider):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[tuple[MediaType, int]] = []

        async def get_country_codes(
            self, media_type: MediaType, tmdb_id: int
        ) -> list[str] | None:
            self.calls.append((media_type, tmdb_id))
            if tmdb_id == 42:
                raise AppError("TMDB_NOT_FOUND", "not found", status_code=404)
            return ["KR"]

    provider = PartiallyMissingProvider()
    processor = JobProcessor(
        None,  # type: ignore[arg-type]
        "worker-country-not-found",
        FakeMediaSource,
        lambda: provider,
    )

    enriched, warnings = await processor._enrich_country_codes(items, [])

    assert provider.calls == [(MediaType.TV, 42), (MediaType.TV, 43)]
    assert [warning.error_code for warning in warnings] == ["COUNTRY_METADATA_ISOLATED"]
    assert [item.country_codes for item in enriched] == [None, None, ["KR"]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_code",
    ("TMDB_VALIDATION_ERROR", "TMDB_RESPONSE_INVALID", "TMDB_HTTP_ERROR"),
)
async def test_country_enrichment_does_not_isolate_systemic_tmdb_errors(
    error_code: str,
) -> None:
    expected = AppError(error_code, "systemic failure", status_code=502)

    class FailingProvider(MockTmdbProvider):
        async def get_country_codes(
            self, media_type: MediaType, tmdb_id: int
        ) -> list[str] | None:
            raise expected

    processor = JobProcessor(
        None,  # type: ignore[arg-type]
        "worker-country-systemic-error",
        FakeMediaSource,
        FailingProvider,
    )

    with pytest.raises(AppError) as caught:
        await processor._enrich_country_codes([media_fixture()], [])

    assert caught.value is expected


@pytest.mark.asyncio
async def test_country_enrichment_wraps_invalid_provider_values() -> None:
    class InvalidProvider(MockTmdbProvider):
        async def get_country_codes(
            self, media_type: MediaType, tmdb_id: int
        ) -> list[str] | None:
            raise ValueError("invalid country payload")

    processor = JobProcessor(
        None,  # type: ignore[arg-type]
        "worker-country-invalid-value",
        FakeMediaSource,
        InvalidProvider,
    )

    with pytest.raises(AppError) as caught:
        await processor._enrich_country_codes([media_fixture()], [])

    assert caught.value.error_code == "TMDB_VALIDATION_ERROR"
    assert caught.value.status_code == 502


@pytest.mark.asyncio
async def test_country_enrichment_without_tmdb_keeps_discovery_usable() -> None:
    processor = JobProcessor(
        None,  # type: ignore[arg-type]
        "worker-country-disabled",
        FakeMediaSource,
    )

    enriched, warnings = await processor._enrich_country_codes([media_fixture()], [])

    assert enriched[0].country_codes is None
    assert [warning.error_code for warning in warnings] == ["COUNTRY_METADATA_UNAVAILABLE"]


@pytest.mark.asyncio
async def test_worker_reuses_persisted_country_codes_before_tmdb_lookup(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add(
            MediaItem(
                source="nextfind",
                source_item_id="nextfind:42",
                media_type=MediaType.TV,
                tmdb_id=42,
                title="Existing",
                country_codes=["JP"],
                identity_confidence=IdentityConfidence.HIGH,
                metadata_status=MetadataStatus.RESOLVED,
                discovered_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    processor = JobProcessor(
        session_factory,
        "worker-country-reuse",
        FakeMediaSource,
    )
    enriched = await processor._reuse_persisted_country_codes([media_fixture()])

    assert enriched[0].country_codes == ["JP"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error_code", ("UPSTREAM_TIMEOUT", "UPSTREAM_NETWORK_ERROR"))
async def test_retryable_failure_enters_retry_wait(
    session_factory: async_sessionmaker[AsyncSession],
    error_code: str,
) -> None:
    async with session_factory() as session:
        run, _ = await create_discovery_run(session)
        await session.commit()
    error = AppError(error_code, "Retryable upstream error", retryable=True)
    processor = JobProcessor(
        session_factory, "worker-1", lambda: FakeMediaSource(error=error)
    )
    assert await processor.run_once() is True
    async with session_factory() as session:
        job = await session.scalar(select(Job))
        refreshed = await session.get(DiscoveryRun, run.id)
        assert job is not None and job.status == JobStatus.RETRY_WAIT
        assert job.error_code == error_code
        assert job.next_retry_at is not None
        assert refreshed is not None and refreshed.status == JobStatus.RETRY_WAIT
        assert refreshed.error_code == error_code


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
async def test_worker_uses_discovery_episode_summary_without_per_item_lookup() -> None:
    item = media_fixture().model_copy(
        update={
            "local_episodes": 3,
            "local_episode_matrix": None,
            "total_episodes": 8,
            "aired_episodes": 6,
            "missing_episodes": None,
        }
    )
    adapter = FakeMediaSource()

    async def unexpected_details(_: MediaType, __: int) -> LibraryDetails:
        raise AssertionError("discovery summary must not trigger a per-item lookup")

    adapter.get_library_details = unexpected_details  # type: ignore[method-assign]

    enriched, warnings = await JobProcessor._enrich_library_details(adapter, [item], [])

    assert enriched == [item]
    assert not warnings


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
async def test_worker_cancels_in_flight_work_when_lease_is_lost(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await create_discovery_run(session)
        await session.commit()

    entered = asyncio.Event()
    cancelled = asyncio.Event()

    class BlockingMediaSource(FakeMediaSource):
        async def list_missing_media(self) -> MediaDiscoveryResult:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
            raise AssertionError("unreachable")

    processor = JobProcessor(
        session_factory,
        "worker-lost-lease",
        BlockingMediaSource,
        lease_seconds=1,
        lease_renew_interval_seconds=0.02,
        heartbeat_interval_seconds=0.01,
    )

    async def lose_lease(job_id: str, lease_token: str) -> bool:
        del job_id, lease_token
        await entered.wait()
        return False

    processor.renew_lease = lose_lease  # type: ignore[method-assign]

    assert await asyncio.wait_for(processor.run_once(), timeout=1) is True
    assert cancelled.is_set()
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(MediaItem)) == 0


@pytest.mark.asyncio
async def test_worker_refreshes_heartbeat_while_job_is_running(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await create_discovery_run(session)
        await session.commit()

    release = asyncio.Event()

    class BlockingMediaSource(FakeMediaSource):
        async def list_missing_media(self) -> MediaDiscoveryResult:
            await release.wait()
            return await super().list_missing_media()

    processor = JobProcessor(
        session_factory,
        "worker-heartbeat",
        BlockingMediaSource,
        lease_seconds=1,
        lease_renew_interval_seconds=0.5,
        heartbeat_interval_seconds=0.02,
    )
    heartbeat_calls = 0
    original_heartbeat = processor.heartbeat

    async def recording_heartbeat() -> None:
        nonlocal heartbeat_calls
        heartbeat_calls += 1
        await original_heartbeat()
        if heartbeat_calls >= 3:
            release.set()

    processor.heartbeat = recording_heartbeat  # type: ignore[method-assign]

    assert await asyncio.wait_for(processor.run_once(), timeout=1) is True
    assert heartbeat_calls >= 3


@pytest.mark.asyncio
async def test_country_checkpoint_updates_only_unknown_rows_owned_by_worker(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    unknown = MediaItem(
        source="nextfind",
        source_item_id="nextfind:42",
        media_type=MediaType.TV,
        tmdb_id=42,
        title="Unknown country",
        identity_confidence=IdentityConfidence.HIGH,
        metadata_status=MetadataStatus.RESOLVED,
        discovered_at=now,
        updated_at=now,
    )
    known = MediaItem(
        source="nextfind",
        source_item_id="nextfind:43",
        media_type=MediaType.TV,
        tmdb_id=43,
        title="Known country",
        country_codes=["US"],
        identity_confidence=IdentityConfidence.HIGH,
        metadata_status=MetadataStatus.RESOLVED,
        discovered_at=now,
        updated_at=now,
    )
    async with session_factory() as session:
        session.add_all([unknown, known])
        await create_discovery_run(session)
        await session.commit()

    processor = JobProcessor(session_factory, "worker-country-checkpoint", FakeMediaSource)
    claimed = await processor.claim_job()
    assert claimed is not None and claimed.lease_token is not None

    await processor._checkpoint_country_codes(
        claimed.id,
        claimed.lease_token,
        {(MediaType.TV, 42): ["JP"], (MediaType.TV, 43): ["KR"]},
    )

    async with session_factory() as session:
        refreshed_unknown = await session.get(MediaItem, unknown.id)
        refreshed_known = await session.get(MediaItem, known.id)
        assert refreshed_unknown is not None
        assert refreshed_unknown.country_codes == ["JP"]
        assert refreshed_known is not None
        assert refreshed_known.country_codes == ["US"]

    with pytest.raises(AppError) as caught:
        await processor._checkpoint_country_codes(
            claimed.id,
            "wrong-token",
            {(MediaType.TV, 42): ["KR"]},
        )
    assert caught.value.error_code == "JOB_LEASE_LOST"


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
