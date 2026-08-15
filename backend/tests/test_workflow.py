from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.metadata.mock_tmdb import MockTmdbProvider
from app.adapters.pt_sites.avistaz import AvistaZMockAdapter
from app.errors import AppError
from app.models.entities import (
    AuditEvent,
    IdentityReview,
    Job,
    MediaItem,
    MetadataMatch,
    TorrentCandidateRecord,
    TorrentSearchRun,
)
from app.models.enums import (
    IdentityConfidence,
    JobStatus,
    MediaType,
    MetadataStatus,
    WorkflowStatus,
)
from app.schemas.adapters import MetadataRecord, TorrentCandidate
from app.schemas.entities import IdentityConfirmationRequest, TorrentSearchCreateRequest
from app.services.workflow import (
    METADATA_RESOLUTION_SUPERSEDED_ERROR_CODE,
    confirm_identity,
    enqueue_metadata_resolution,
    enqueue_torrent_search,
    metadata_resolution_input_fingerprint,
)
from app.workers.processor import JobProcessor
from tests.test_discovery_worker import FakeMediaSource


class EnabledAvistaZMockAdapter(AvistaZMockAdapter):
    def manifest(self):
        return super().manifest().model_copy(update={"enabled": True})


def record(
    tmdb_id: int,
    *,
    media_type: MediaType = MediaType.MOVIE,
    episode_matrix: dict[int, list[int]] | None = None,
    country_codes: list[str] | None = None,
) -> MetadataRecord:
    return MetadataRecord(
        tmdb_id=tmdb_id,
        imdb_id=f"tt{tmdb_id:07d}",
        media_type=media_type,
        title="测试电影" if media_type == MediaType.MOVIE else "测试剧",
        chinese_title="测试电影" if media_type == MediaType.MOVIE else "测试剧",
        english_title="Test Movie" if media_type == MediaType.MOVIE else "Test Show",
        original_title="Original",
        year=2026,
        episode_matrix=episode_matrix,
        country_codes=country_codes,
        confidence=1,
    )


def media(*, tmdb_id: int | None = 10, media_type: MediaType = MediaType.MOVIE) -> MediaItem:
    now = datetime.now(UTC)
    return MediaItem(
        source="nextfind",
        source_item_id=f"nextfind:{tmdb_id or 'temporary'}",
        media_type=media_type,
        tmdb_id=tmdb_id,
        title="测试电影" if media_type == MediaType.MOVIE else "测试剧",
        year=2026,
        missing_episodes=["S01E03"] if media_type == MediaType.TV else None,
        identity_confidence=(
            IdentityConfidence.HIGH
            if tmdb_id is not None
            else IdentityConfidence.NEEDS_CONFIRMATION
        ),
        metadata_status=MetadataStatus.UNRESOLVED,
        discovered_at=now,
        updated_at=now,
    )


class RecordingProvider(MockTmdbProvider):
    def __init__(self, fixtures: list[MetadataRecord]) -> None:
        super().__init__(fixtures)
        self.get_calls = 0
        self.search_calls = 0

    async def get_by_tmdb_id(self, media_type: MediaType, tmdb_id: int) -> MetadataRecord:
        self.get_calls += 1
        return await super().get_by_tmdb_id(media_type, tmdb_id)

    async def search(
        self, media_type: MediaType, title: str, year: int | None = None
    ) -> list[MetadataRecord]:
        self.search_calls += 1
        return await super().search(media_type, title, year)


class ConflictProvider(RecordingProvider):
    async def get_by_tmdb_id(self, media_type: MediaType, tmdb_id: int) -> MetadataRecord:
        self.get_calls += 1
        if media_type == MediaType.MOVIE:
            raise AppError("TMDB_NOT_FOUND", "not found", status_code=404)
        return record(tmdb_id, media_type=MediaType.TV)


@pytest.mark.asyncio
async def test_existing_tmdb_id_uses_exact_details_without_fuzzy_search(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    item = media(tmdb_id=10)
    async with session_factory() as session:
        session.add(item)
        await session.commit()
        await enqueue_metadata_resolution(session, item, max_attempts=3)
        await session.commit()
    provider = RecordingProvider([record(10)])
    processor = JobProcessor(
        session_factory,
        "worker-metadata",
        FakeMediaSource,
        lambda: provider,
    )
    assert await processor.run_once() is True
    assert provider.get_calls == 1
    assert provider.search_calls == 0
    async with session_factory() as session:
        refreshed = await session.get(MediaItem, item.id)
        match = await session.scalar(select(MetadataMatch))
        review = await session.scalar(select(IdentityReview))
        assert refreshed is not None
        assert refreshed.workflow_status == WorkflowStatus.IDENTITY_CONFIRMED
        assert refreshed.metadata_status == MetadataStatus.RESOLVED
        assert match is not None and "TMDB_ID_EXACT" in match.match_reasons
        assert review is not None
        assert review.metadata_match_id == match.id
        assert review.status == "CONFIRMED"
        assert review.confirmed_by == "system:strict-identity"

        run, search_job, deduplicated = await enqueue_torrent_search(
            session,
            refreshed,
            TorrentSearchCreateRequest(site_id="avistaz"),
            max_attempts=3,
        )
        assert deduplicated is False
        assert run.media_id == refreshed.id
        assert search_job.payload["media_id"] == refreshed.id


@pytest.mark.asyncio
async def test_identity_confirmation_persists_candidate_country_codes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    item = media(tmdb_id=None)
    candidate = record(12, country_codes=["JP", "US"])
    async with session_factory() as session:
        session.add(item)
        await session.flush()
        match = MetadataMatch(
            media_id=item.id,
            tmdb_id=candidate.tmdb_id,
            rank=1,
            score=1,
            match_reasons=["TITLE_EXACT"],
            conflicts=[],
            candidate_snapshot=candidate.model_dump(mode="json"),
        )
        session.add(match)
        await session.flush()
        await confirm_identity(
            session,
            item,
            IdentityConfirmationRequest(metadata_match_id=match.id),
            actor="test-operator",
        )
        await session.commit()

    async with session_factory() as session:
        refreshed = await session.get(MediaItem, item.id)
        assert refreshed is not None
        assert refreshed.country_codes == ["JP", "US"]


@pytest.mark.asyncio
async def test_identity_confirmation_preserves_nextfind_country_for_same_identity(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    item = media(tmdb_id=11)
    item.country_codes = ["JP"]
    candidate = record(11, country_codes=["US"])
    async with session_factory() as session:
        session.add(item)
        await session.flush()
        match = MetadataMatch(
            media_id=item.id,
            tmdb_id=candidate.tmdb_id,
            rank=1,
            score=1,
            match_reasons=["TMDB_ID_EXACT"],
            conflicts=[],
            candidate_snapshot=candidate.model_dump(mode="json"),
        )
        session.add(match)
        await session.flush()
        await confirm_identity(
            session,
            item,
            IdentityConfirmationRequest(metadata_match_id=match.id),
            actor="test-operator",
        )
        await session.commit()

    async with session_factory() as session:
        refreshed = await session.get(MediaItem, item.id)
        assert refreshed is not None
        assert refreshed.country_codes == ["JP"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("candidate_tmdb_id", "candidate_type", "expected_country_codes"),
    (
        (12, MediaType.MOVIE, None),
        (11, MediaType.TV, None),
        (11, MediaType.MOVIE, ["JP"]),
    ),
)
async def test_identity_confirmation_does_not_keep_country_from_another_identity(
    session_factory: async_sessionmaker[AsyncSession],
    candidate_tmdb_id: int,
    candidate_type: MediaType,
    expected_country_codes: list[str] | None,
) -> None:
    item = media(tmdb_id=11)
    item.country_codes = ["JP"]
    candidate = record(candidate_tmdb_id, media_type=candidate_type)
    async with session_factory() as session:
        session.add(item)
        await session.flush()
        match = MetadataMatch(
            media_id=item.id,
            tmdb_id=candidate.tmdb_id,
            rank=1,
            score=1,
            match_reasons=["TITLE_EXACT"],
            conflicts=[],
            candidate_snapshot=candidate.model_dump(mode="json"),
        )
        session.add(match)
        await session.flush()
        await confirm_identity(
            session,
            item,
            IdentityConfirmationRequest(metadata_match_id=match.id),
            actor="test-operator",
        )
        await session.commit()

    async with session_factory() as session:
        refreshed = await session.get(MediaItem, item.id)
        assert refreshed is not None
        assert refreshed.tmdb_id == candidate_tmdb_id
        assert refreshed.media_type == candidate_type
        assert refreshed.country_codes == expected_country_codes


@pytest.mark.asyncio
async def test_type_conflict_enters_manual_review(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    item = media(tmdb_id=11, media_type=MediaType.MOVIE)
    async with session_factory() as session:
        session.add(item)
        await session.commit()
        await enqueue_metadata_resolution(session, item, max_attempts=3)
        await session.commit()
    provider = ConflictProvider([])
    processor = JobProcessor(
        session_factory,
        "worker-conflict",
        FakeMediaSource,
        lambda: provider,
    )
    assert await processor.run_once() is True
    async with session_factory() as session:
        refreshed = await session.get(MediaItem, item.id)
        match = await session.scalar(select(MetadataMatch))
        review_count = await session.scalar(select(func.count()).select_from(IdentityReview))
        assert refreshed is not None
        assert refreshed.workflow_status == WorkflowStatus.IDENTITY_REVIEW
        assert refreshed.metadata_status == MetadataStatus.NEEDS_CONFIRMATION
        assert match is not None
        assert "MEDIA_TYPE_CONFLICT" in match.conflicts
        assert review_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("media_updates", "candidate_updates"),
    (
        ({"year": None}, {}),
        ({}, {"year": None}),
        ({}, {"year": 2025}),
        ({"title": "错误片名"}, {}),
        (
            {"original_title": "NextFind Original"},
            {"original_title": "Different Original"},
        ),
        ({"identity_confidence": IdentityConfidence.NEEDS_CONFIRMATION}, {}),
    ),
)
async def test_strict_identity_auto_confirmation_keeps_incomplete_or_conflicting_signals_manual(
    session_factory: async_sessionmaker[AsyncSession],
    media_updates: dict[str, object],
    candidate_updates: dict[str, object],
) -> None:
    item = media(tmdb_id=13)
    for field, value in media_updates.items():
        setattr(item, field, value)
    candidate = record(13).model_copy(update=candidate_updates)
    async with session_factory() as session:
        session.add(item)
        await session.commit()
        await enqueue_metadata_resolution(session, item, max_attempts=3)
        await session.commit()
    processor = JobProcessor(
        session_factory,
        "worker-strict-identity-manual",
        FakeMediaSource,
        lambda: RecordingProvider([candidate]),
    )

    assert await processor.run_once() is True

    async with session_factory() as session:
        refreshed = await session.get(MediaItem, item.id)
        review_count = await session.scalar(select(func.count()).select_from(IdentityReview))
        ready_event = await session.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "METADATA_CANDIDATES_READY")
        )
        assert refreshed is not None
        assert refreshed.workflow_status == WorkflowStatus.IDENTITY_REVIEW
        assert refreshed.metadata_status == MetadataStatus.NEEDS_CONFIRMATION
        assert review_count == 0
        assert ready_event is not None
        assert ready_event.sanitized_details["manual_confirmation_required"] is True
        assert ready_event.sanitized_details["strict_auto_confirmed"] is False


@pytest.mark.asyncio
async def test_exact_tv_metadata_derives_missing_from_aired_minus_local_matrix(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    item = media(tmdb_id=15, media_type=MediaType.TV)
    item.local_episodes = 3
    item.local_episode_matrix = {"1": [1, 3], "2": [1]}
    item.missing_episodes = ["S09E09"]
    async with session_factory() as session:
        session.add(item)
        await session.commit()
        await enqueue_metadata_resolution(session, item, max_attempts=3)
        await session.commit()
    provider = RecordingProvider(
        [
            record(
                15,
                media_type=MediaType.TV,
                episode_matrix={1: [1, 2, 3, 4], 2: [1, 2]},
            )
        ]
    )
    processor = JobProcessor(
        session_factory,
        "worker-tv-matrix",
        FakeMediaSource,
        lambda: provider,
    )

    assert await processor.run_once() is True

    async with session_factory() as session:
        refreshed = await session.get(MediaItem, item.id)
        assert refreshed is not None
        assert refreshed.missing_episodes == ["S01E02", "S01E04", "S02E02"]


@pytest.mark.asyncio
async def test_unknown_tmdb_episode_matrix_preserves_upstream_missing_episodes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    item = media(tmdb_id=17, media_type=MediaType.TV)
    item.local_episode_matrix = {1: [1, 2]}
    item.missing_episodes = ["S01E03"]
    async with session_factory() as session:
        session.add(item)
        await session.commit()
        await enqueue_metadata_resolution(session, item, max_attempts=3)
        await session.commit()
    provider = RecordingProvider(
        [record(17, media_type=MediaType.TV, episode_matrix=None)]
    )
    processor = JobProcessor(
        session_factory,
        "worker-tv-unknown-matrix",
        FakeMediaSource,
        lambda: provider,
    )

    assert await processor.run_once() is True

    async with session_factory() as session:
        refreshed = await session.get(MediaItem, item.id)
        assert refreshed is not None
        assert refreshed.missing_episodes == ["S01E03"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("upstream_missing", "expected"),
    ((["S01E03"], ["S01E03"]), (None, None)),
)
async def test_tv_metadata_never_guesses_missing_from_episode_counts(
    session_factory: async_sessionmaker[AsyncSession],
    upstream_missing: list[str] | None,
    expected: list[str] | None,
) -> None:
    item = media(tmdb_id=16, media_type=MediaType.TV)
    item.local_episodes = 3
    item.aired_episodes = 6
    item.local_episode_matrix = None
    item.missing_episodes = upstream_missing
    async with session_factory() as session:
        session.add(item)
        await session.commit()
        await enqueue_metadata_resolution(session, item, max_attempts=3)
        await session.commit()
    provider = RecordingProvider(
        [record(16, media_type=MediaType.TV, episode_matrix={1: [1, 2, 3, 4, 5, 6]})]
    )
    processor = JobProcessor(
        session_factory,
        "worker-tv-counts",
        FakeMediaSource,
        lambda: provider,
    )

    assert await processor.run_once() is True

    async with session_factory() as session:
        refreshed = await session.get(MediaItem, item.id)
        assert refreshed is not None
        assert refreshed.missing_episodes == expected


@pytest.mark.asyncio
async def test_missing_tmdb_id_returns_candidates_without_auto_writing_identity(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    item = media(tmdb_id=None)
    async with session_factory() as session:
        session.add(item)
        await session.commit()
        await enqueue_metadata_resolution(session, item, max_attempts=3)
        await session.commit()
    provider = RecordingProvider([record(21), record(22)])
    processor = JobProcessor(
        session_factory,
        "worker-search",
        FakeMediaSource,
        lambda: provider,
    )
    assert await processor.run_once() is True
    assert provider.search_calls == 1 and provider.get_calls == 0
    async with session_factory() as session:
        refreshed = await session.get(MediaItem, item.id)
        count = await session.scalar(select(func.count()).select_from(MetadataMatch))
        review_count = await session.scalar(select(func.count()).select_from(IdentityReview))
        assert refreshed is not None and refreshed.tmdb_id is None
        assert refreshed.workflow_status == WorkflowStatus.IDENTITY_REVIEW
        assert count == 2
        assert review_count == 0


@pytest.mark.asyncio
async def test_manual_confirmation_is_audited_and_duplicate_is_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    item = media(tmdb_id=None)
    candidate = record(30)
    async with session_factory() as session:
        session.add(item)
        await session.flush()
        match = MetadataMatch(
            media_id=item.id,
            tmdb_id=30,
            rank=1,
            score=0.9,
            match_reasons=["TITLE_EXACT"],
            conflicts=[],
            candidate_snapshot=candidate.model_dump(mode="json"),
        )
        session.add(match)
        await session.commit()
        review = await confirm_identity(
            session,
            item,
            IdentityConfirmationRequest(metadata_match_id=match.id),
            actor="operator-a",
        )
        await session.commit()
        assert review.confirmed_by == "operator-a"
        with pytest.raises(AppError) as caught:
            await confirm_identity(
                session,
                item,
                IdentityConfirmationRequest(metadata_match_id=match.id),
                actor="operator-a",
            )
        assert caught.value.error_code == "IDENTITY_CONFIRMATION_DUPLICATE"


@pytest.mark.asyncio
async def test_manual_tv_identity_confirmation_derives_missing_from_selected_candidate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    item = media(tmdb_id=None, media_type=MediaType.TV)
    item.local_episode_matrix = {"1": [1, 3]}
    candidate = record(
        31,
        media_type=MediaType.TV,
        episode_matrix={1: [1, 2, 3, 4]},
    )
    async with session_factory() as session:
        session.add(item)
        await session.flush()
        match = MetadataMatch(
            media_id=item.id,
            tmdb_id=31,
            rank=1,
            score=0.9,
            match_reasons=["TITLE_EXACT"],
            conflicts=[],
            candidate_snapshot=candidate.model_dump(mode="json"),
        )
        session.add(match)
        await session.commit()

        await confirm_identity(
            session,
            item,
            IdentityConfirmationRequest(metadata_match_id=match.id),
            actor="operator-a",
        )
        await session.commit()

        assert item.missing_episodes == ["S01E02", "S01E04"]


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence_kind", ("confirmed_review", "run_only"))
async def test_metadata_resolution_rejects_durable_identity_evidence_without_writes(
    session_factory: async_sessionmaker[AsyncSession],
    evidence_kind: str,
) -> None:
    item = media(tmdb_id=70)
    item.workflow_status = WorkflowStatus.IDENTITY_REVIEW
    item.metadata_status = MetadataStatus.RESOLVED
    candidate = record(70)
    async with session_factory() as session:
        session.add(item)
        await session.flush()
        if evidence_kind == "confirmed_review":
            match = MetadataMatch(
                media_id=item.id,
                tmdb_id=70,
                rank=1,
                score=1,
                match_reasons=["TMDB_ID_EXACT"],
                conflicts=[],
                candidate_snapshot=candidate.model_dump(mode="json"),
            )
            session.add(match)
            await session.flush()
            session.add(
                IdentityReview(
                    media_id=item.id,
                    metadata_match_id=match.id,
                    status="CONFIRMED",
                    confirmed_by="operator-a",
                    candidate_snapshot=candidate.model_dump(mode="json"),
                )
            )
        else:
            session.add(
                TorrentSearchRun(
                    media_id=item.id,
                    site_id="avistaz",
                    status=WorkflowStatus.TORRENT_REVIEW,
                    sanitized_request={},
                )
            )
        await session.commit()

        jobs_before = await session.scalar(select(func.count()).select_from(Job))
        audits_before = await session.scalar(select(func.count()).select_from(AuditEvent))
        matches_before = await session.scalar(select(func.count()).select_from(MetadataMatch))
        with pytest.raises(AppError) as caught:
            await enqueue_metadata_resolution(session, item, max_attempts=3)

        assert caught.value.error_code == "IDENTITY_ALREADY_CONFIRMED"
        assert caught.value.status_code == 409
        assert not session.new
        assert not session.dirty
        assert await session.scalar(select(func.count()).select_from(Job)) == jobs_before
        assert await session.scalar(select(func.count()).select_from(AuditEvent)) == audits_before
        assert (
            await session.scalar(select(func.count()).select_from(MetadataMatch))
            == matches_before
        )
        assert item.workflow_status == WorkflowStatus.IDENTITY_REVIEW
        assert item.metadata_status == MetadataStatus.RESOLVED


@pytest.mark.asyncio
async def test_run_only_history_blocks_identity_replacement_and_new_search_without_writes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    item = media(tmdb_id=80)
    item.workflow_status = WorkflowStatus.TORRENT_REVIEW
    candidate = record(81)
    async with session_factory() as session:
        session.add(item)
        await session.flush()
        match = MetadataMatch(
            media_id=item.id,
            tmdb_id=81,
            rank=1,
            score=0.9,
            match_reasons=["TITLE_EXACT"],
            conflicts=[],
            candidate_snapshot=candidate.model_dump(mode="json"),
        )
        session.add_all(
            [
                match,
                TorrentSearchRun(
                    media_id=item.id,
                    site_id="avistaz",
                    status=WorkflowStatus.TORRENT_REVIEW,
                    sanitized_request={},
                ),
            ]
        )
        await session.commit()
        jobs_before = await session.scalar(select(func.count()).select_from(Job))
        audits_before = await session.scalar(select(func.count()).select_from(AuditEvent))
        runs_before = await session.scalar(select(func.count()).select_from(TorrentSearchRun))

        with pytest.raises(AppError) as confirmation_error:
            await confirm_identity(
                session,
                item,
                IdentityConfirmationRequest(metadata_match_id=match.id),
                actor="operator-a",
            )
        assert confirmation_error.value.error_code == "IDENTITY_CONFIRMATION_DUPLICATE"

        with pytest.raises(AppError) as search_error:
            await enqueue_torrent_search(
                session,
                item,
                TorrentSearchCreateRequest(site_id="avistaz"),
                max_attempts=3,
            )
        assert search_error.value.error_code == "IDENTITY_CONFIRMATION_REQUIRED"
        assert not session.new
        assert not session.dirty
        assert await session.scalar(select(func.count()).select_from(Job)) == jobs_before
        assert await session.scalar(select(func.count()).select_from(AuditEvent)) == audits_before
        assert (
            await session.scalar(select(func.count()).select_from(TorrentSearchRun))
            == runs_before
        )
        assert await session.scalar(select(func.count()).select_from(IdentityReview)) == 0
        assert item.tmdb_id == 80
        assert item.workflow_status == WorkflowStatus.TORRENT_REVIEW


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "audit_event_type",
    ("IDENTITY_CONFIRMED_MANUALLY", "IDENTITY_CONFIRMED_AUTOMATICALLY"),
)
async def test_confirmation_cancels_active_resolution_and_late_completion_is_inert(
    session_factory: async_sessionmaker[AsyncSession],
    audit_event_type: str,
) -> None:
    item = media(tmdb_id=90)
    candidate = record(90)
    processor = JobProcessor(
        session_factory,
        "late-metadata-worker",
        FakeMediaSource,
        lambda: MockTmdbProvider(),
    )
    lease_token = "late-metadata-lease"
    async with session_factory() as session:
        session.add(item)
        await session.flush()
        match = MetadataMatch(
            media_id=item.id,
            tmdb_id=90,
            rank=1,
            score=1,
            match_reasons=["TMDB_ID_EXACT"],
            conflicts=[],
            candidate_snapshot=candidate.model_dump(mode="json"),
        )
        session.add(match)
        await session.commit()
        job, deduplicated = await enqueue_metadata_resolution(session, item, max_attempts=3)
        assert deduplicated is False
        job.status = JobStatus.RUNNING
        job.attempts = 1
        job.locked_at = datetime.now(UTC)
        job.locked_by = processor.worker_id
        job.lease_token = lease_token
        await session.commit()

        await confirm_identity(
            session,
            item,
            IdentityConfirmationRequest(metadata_match_id=match.id),
            actor="operator-a",
            audit_event_type=audit_event_type,
        )
        await session.commit()

        cancelled = await session.get(Job, job.id)
        assert cancelled is not None
        assert cancelled.status == JobStatus.CANCELLED
        assert cancelled.error_code == METADATA_RESOLUTION_SUPERSEDED_ERROR_CODE
        assert cancelled.locked_at is None
        assert cancelled.locked_by is None
        assert cancelled.lease_token is None
        assert item.workflow_status == WorkflowStatus.IDENTITY_CONFIRMED
        assert item.metadata_status == MetadataStatus.RESOLVED
        matches_before = await session.scalar(select(func.count()).select_from(MetadataMatch))
        audits_before = await session.scalar(select(func.count()).select_from(AuditEvent))
        cancellation_events = list(
            (
                await session.scalars(
                    select(AuditEvent).where(
                        AuditEvent.entity_id == job.id,
                        AuditEvent.event_type == "METADATA_RESOLUTION_CANCELLED",
                    )
                )
            ).all()
        )
        assert len(cancellation_events) == 1
        assert cancellation_events[0].sanitized_details["error_code"] == (
            METADATA_RESOLUTION_SUPERSEDED_ERROR_CODE
        )

    await processor._complete_metadata_resolution(
        job.id,
        item.id,
        [candidate],
        lease_token=lease_token,
    )

    async with session_factory() as session:
        refreshed = await session.get(MediaItem, item.id)
        assert refreshed is not None
        assert refreshed.workflow_status == WorkflowStatus.IDENTITY_CONFIRMED
        assert refreshed.metadata_status == MetadataStatus.RESOLVED
        assert (
            await session.scalar(select(func.count()).select_from(MetadataMatch))
            == matches_before
        )
        assert await session.scalar(select(func.count()).select_from(AuditEvent)) == audits_before
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.event_type == "METADATA_CANDIDATES_READY")
            )
            == 0
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("evidence_kind", "expected_status", "expected_evidence"),
    (
        (
            "confirmed_review",
            WorkflowStatus.IDENTITY_CONFIRMED,
            "CONFIRMED_IDENTITY_REVIEW",
        ),
        ("run_only", WorkflowStatus.TORRENT_REVIEW, "TORRENT_SEARCH_HISTORY"),
    ),
)
async def test_late_completion_cancels_finalized_identity_job_without_candidate_pollution(
    session_factory: async_sessionmaker[AsyncSession],
    evidence_kind: str,
    expected_status: WorkflowStatus,
    expected_evidence: str,
) -> None:
    item = media(tmdb_id=91)
    item.workflow_status = expected_status
    item.metadata_status = MetadataStatus.RESOLVED
    candidate = record(91)
    processor = JobProcessor(
        session_factory,
        "legacy-run-metadata-worker",
        FakeMediaSource,
        lambda: MockTmdbProvider(),
    )
    lease_token = "legacy-run-metadata-lease"
    async with session_factory() as session:
        session.add(item)
        await session.flush()
        job = Job(
            job_type=f"RESOLVE_METADATA:{item.id}",
            status=JobStatus.RUNNING,
            payload={
                "media_id": item.id,
                "input_fingerprint": metadata_resolution_input_fingerprint(item),
                "read_only": True,
            },
            attempts=1,
            max_attempts=3,
            locked_at=datetime.now(UTC),
            locked_by=processor.worker_id,
            lease_token=lease_token,
        )
        session.add(job)
        if evidence_kind == "confirmed_review":
            match = MetadataMatch(
                media_id=item.id,
                tmdb_id=91,
                rank=1,
                score=1,
                match_reasons=["TMDB_ID_EXACT"],
                conflicts=[],
                candidate_snapshot=candidate.model_dump(mode="json"),
            )
            session.add(match)
            await session.flush()
            session.add(
                IdentityReview(
                    media_id=item.id,
                    metadata_match_id=match.id,
                    status="CONFIRMED",
                    confirmed_by="legacy-confirmation",
                    candidate_snapshot=candidate.model_dump(mode="json"),
                )
            )
        else:
            session.add(
                TorrentSearchRun(
                    media_id=item.id,
                    site_id="avistaz",
                    status=WorkflowStatus.TORRENT_REVIEW,
                    sanitized_request={},
                )
            )
        await session.commit()
        matches_before = await session.scalar(select(func.count()).select_from(MetadataMatch))

    await processor._complete_metadata_resolution(
        job.id,
        item.id,
        [candidate],
        lease_token=lease_token,
    )

    async with session_factory() as session:
        refreshed = await session.get(MediaItem, item.id)
        cancelled = await session.get(Job, job.id)
        assert refreshed is not None
        assert refreshed.workflow_status == expected_status
        assert refreshed.metadata_status == MetadataStatus.RESOLVED
        assert cancelled is not None
        assert cancelled.status == JobStatus.CANCELLED
        assert cancelled.error_code == METADATA_RESOLUTION_SUPERSEDED_ERROR_CODE
        assert cancelled.locked_at is None
        assert cancelled.locked_by is None
        assert cancelled.lease_token is None
        assert (
            await session.scalar(select(func.count()).select_from(MetadataMatch))
            == matches_before
        )
        events = list((await session.scalars(select(AuditEvent))).all())
        assert [event.event_type for event in events] == ["METADATA_RESOLUTION_CANCELLED"]
        assert events[0].entity_id == job.id
        assert events[0].sanitized_details["evidence"] == expected_evidence


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("evidence_kind", "expected_status", "expected_evidence"),
    (
        (
            "confirmed_review",
            WorkflowStatus.IDENTITY_CONFIRMED,
            "CONFIRMED_IDENTITY_REVIEW",
        ),
        ("run_only", WorkflowStatus.TORRENT_REVIEW, "TORRENT_SEARCH_HISTORY"),
    ),
)
async def test_finalized_identity_cancels_legacy_resolution_before_provider_factory(
    session_factory: async_sessionmaker[AsyncSession],
    evidence_kind: str,
    expected_status: WorkflowStatus,
    expected_evidence: str,
) -> None:
    item = media(tmdb_id=92)
    item.workflow_status = expected_status
    item.metadata_status = MetadataStatus.RESOLVED
    candidate = record(92)
    provider_factory_calls = 0

    def forbidden_provider_factory() -> MockTmdbProvider:
        nonlocal provider_factory_calls
        provider_factory_calls += 1
        raise AssertionError("finalized identity must be checked before provider creation")

    processor = JobProcessor(
        session_factory,
        "legacy-finalized-metadata-worker",
        FakeMediaSource,
        forbidden_provider_factory,
    )
    async with session_factory() as session:
        session.add(item)
        await session.flush()
        job = Job(
            job_type=f"RESOLVE_METADATA:{item.id}",
            status=JobStatus.PENDING,
            payload={
                "media_id": item.id,
                "input_fingerprint": metadata_resolution_input_fingerprint(item),
                "read_only": True,
            },
            max_attempts=3,
        )
        session.add(job)
        if evidence_kind == "confirmed_review":
            match = MetadataMatch(
                media_id=item.id,
                tmdb_id=92,
                rank=1,
                score=1,
                match_reasons=["TMDB_ID_EXACT"],
                conflicts=[],
                candidate_snapshot=candidate.model_dump(mode="json"),
            )
            session.add(match)
            await session.flush()
            session.add(
                IdentityReview(
                    media_id=item.id,
                    metadata_match_id=match.id,
                    status="CONFIRMED",
                    confirmed_by="legacy-confirmation",
                    candidate_snapshot=candidate.model_dump(mode="json"),
                )
            )
        else:
            session.add(
                TorrentSearchRun(
                    media_id=item.id,
                    site_id="avistaz",
                    status=WorkflowStatus.TORRENT_REVIEW,
                    sanitized_request={},
                )
            )
        await session.commit()

    assert await processor.run_once() is True
    assert provider_factory_calls == 0

    async with session_factory() as session:
        refreshed = await session.get(MediaItem, item.id)
        cancelled = await session.get(Job, job.id)
        assert refreshed is not None
        assert refreshed.workflow_status == expected_status
        assert refreshed.metadata_status == MetadataStatus.RESOLVED
        assert cancelled is not None
        assert cancelled.status == JobStatus.CANCELLED
        assert cancelled.error_code == METADATA_RESOLUTION_SUPERSEDED_ERROR_CODE
        events = list(
            (
                await session.scalars(
                    select(AuditEvent).where(
                        AuditEvent.entity_id == job.id,
                        AuditEvent.event_type == "METADATA_RESOLUTION_CANCELLED",
                    )
                )
            ).all()
        )
        assert len(events) == 1
        assert events[0].sanitized_details["evidence"] == expected_evidence


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("evidence_kind", "expected_status"),
    (
        ("confirmed_review", WorkflowStatus.IDENTITY_CONFIRMED),
        ("run_only", WorkflowStatus.TORRENT_REVIEW),
    ),
)
async def test_metadata_failure_recheck_preserves_finalized_identity(
    session_factory: async_sessionmaker[AsyncSession],
    evidence_kind: str,
    expected_status: WorkflowStatus,
) -> None:
    item = media(tmdb_id=93)
    item.workflow_status = expected_status
    item.metadata_status = MetadataStatus.RESOLVED
    candidate = record(93)
    processor = JobProcessor(
        session_factory,
        "late-failing-metadata-worker",
        FakeMediaSource,
        lambda: MockTmdbProvider(),
    )
    lease_token = "late-failing-metadata-lease"
    async with session_factory() as session:
        session.add(item)
        await session.flush()
        job = Job(
            job_type=f"RESOLVE_METADATA:{item.id}",
            status=JobStatus.RUNNING,
            payload={
                "media_id": item.id,
                "input_fingerprint": metadata_resolution_input_fingerprint(item),
                "read_only": True,
            },
            attempts=1,
            max_attempts=3,
            locked_at=datetime.now(UTC),
            locked_by=processor.worker_id,
            lease_token=lease_token,
        )
        session.add(job)
        if evidence_kind == "confirmed_review":
            match = MetadataMatch(
                media_id=item.id,
                tmdb_id=93,
                rank=1,
                score=1,
                match_reasons=["TMDB_ID_EXACT"],
                conflicts=[],
                candidate_snapshot=candidate.model_dump(mode="json"),
            )
            session.add(match)
            await session.flush()
            session.add(
                IdentityReview(
                    media_id=item.id,
                    metadata_match_id=match.id,
                    status="CONFIRMED",
                    confirmed_by="legacy-confirmation",
                    candidate_snapshot=candidate.model_dump(mode="json"),
                )
            )
        else:
            session.add(
                TorrentSearchRun(
                    media_id=item.id,
                    site_id="avistaz",
                    status=WorkflowStatus.TORRENT_REVIEW,
                    sanitized_request={},
                )
            )
        await session.commit()

    await processor._fail(
        job.id,
        AppError(
            "TMDB_UNAVAILABLE",
            "TMDB unavailable",
            retryable=True,
            details={"retry_after_seconds": 120},
        ),
        lease_token=lease_token,
    )

    async with session_factory() as session:
        refreshed = await session.get(MediaItem, item.id)
        cancelled = await session.get(Job, job.id)
        assert refreshed is not None
        assert refreshed.workflow_status == expected_status
        assert refreshed.metadata_status == MetadataStatus.RESOLVED
        assert cancelled is not None
        assert cancelled.status == JobStatus.CANCELLED
        assert cancelled.error_code == METADATA_RESOLUTION_SUPERSEDED_ERROR_CODE
        assert cancelled.next_retry_at is None
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.event_type == "METADATA_RESOLUTION_RETRY_SCHEDULED")
            )
            == 0
        )


@pytest.mark.asyncio
async def test_torrent_search_persists_only_sanitized_scored_candidates(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    item = media(tmdb_id=40, media_type=MediaType.TV)
    metadata = record(40, media_type=MediaType.TV)
    async with session_factory() as session:
        session.add(item)
        await session.flush()
        match = MetadataMatch(
            media_id=item.id,
            tmdb_id=40,
            rank=1,
            score=1,
            match_reasons=["TMDB_ID_EXACT"],
            conflicts=[],
            candidate_snapshot=metadata.model_dump(mode="json"),
        )
        session.add(match)
        await session.flush()
        session.add(
            IdentityReview(
                media_id=item.id,
                metadata_match_id=match.id,
                status="CONFIRMED",
                confirmed_by="operator-a",
                candidate_snapshot=metadata.model_dump(mode="json"),
            )
        )
        item.workflow_status = WorkflowStatus.IDENTITY_CONFIRMED
        await session.commit()
        run, _, _ = await enqueue_torrent_search(
            session,
            item,
            TorrentSearchCreateRequest(
                site_id="avistaz",
                preferred_resolutions=["1080p"],
                preferred_subtitles=["Chinese"],
            ),
            max_attempts=3,
        )
        await session.commit()

    fixture = TorrentCandidate(
        site_id="avistaz",
        torrent_id="torrent-40",
        release_title="Test Show 2026 S01E03 1080p WEB-DL",
        details_ref="avistaz:details:safe",
        media_type=MediaType.TV,
        tmdb_id=40,
        year=2026,
        season=1,
        episodes=[3],
        resolution="1080p",
        source="WEB-DL",
        subtitles=["Chinese"],
        seeders=5,
        hit_and_run=False,
    )
    pt = EnabledAvistaZMockAdapter([fixture], manifest_id="avistaz")
    processor = JobProcessor(
        session_factory,
        "worker-pt",
        FakeMediaSource,
        lambda: MockTmdbProvider(),
        lambda: pt,
    )
    assert await processor.run_once() is True
    async with session_factory() as session:
        refreshed_run = await session.get(TorrentSearchRun, run.id)
        stored = await session.scalar(select(TorrentCandidateRecord))
        assert refreshed_run is not None
        assert refreshed_run.status == WorkflowStatus.TORRENT_REVIEW
        assert stored is not None and stored.match_score > 0
        serialized = str(stored.candidate_snapshot).casefold()
        assert "https://" not in serialized
        assert "announce" not in serialized
        assert "passkey" not in serialized
