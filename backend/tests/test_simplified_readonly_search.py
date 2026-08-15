from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.workers.processor as processor_module
from app.adapters.metadata.mock_tmdb import MockTmdbProvider
from app.core.config import Settings
from app.models.entities import (
    ApprovalRequest,
    AutomationDecision,
    IdentityReview,
    Job,
    MediaItem,
    MetadataMatch,
    TorrentCandidateRecord,
    TorrentSearchRun,
)
from app.models.enums import (
    AutomationMode,
    IdentityConfidence,
    JobStatus,
    MediaType,
    MetadataStatus,
    WorkflowStatus,
)
from app.schemas.adapters import MetadataRecord, TorrentCandidate
from app.schemas.automation import AutomationPolicyRevisionCreateRequest
from app.services.automation import maybe_automate_torrent_search_after_identity
from app.services.automation_policy import get_current_policy, publish_policy_revision
from app.services.workflow import enqueue_metadata_resolution
from app.workers.processor import JobProcessor
from tests.test_discovery_worker import FakeMediaSource


def _ready_pt_settings(*, automation_enabled: bool = False) -> Settings:
    return Settings(
        _env_file=None,
        enable_automation_engine=automation_enabled,
        enable_avistaz_live_search=True,
        avistaz_username="test-user",
        avistaz_password="test-password",
        avistaz_pid="test-pid",
        pt_site_architecture="avistaz",
        pt_site_runtime_supported=True,
    )


async def _seed_confirmed_media(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[MediaItem, IdentityReview]:
    now = datetime.now(UTC)
    media = MediaItem(
        source="nextfind",
        source_item_id="nextfind:simplified-readonly-search",
        media_type=MediaType.MOVIE,
        tmdb_id=42,
        title="Test Movie",
        year=2026,
        identity_confidence=IdentityConfidence.HIGH,
        metadata_status=MetadataStatus.RESOLVED,
        workflow_status=WorkflowStatus.IDENTITY_CONFIRMED,
        discovered_at=now,
        updated_at=now,
    )
    metadata = MetadataRecord(
        tmdb_id=42,
        imdb_id="tt0000042",
        media_type=MediaType.MOVIE,
        title="Test Movie",
        english_title="Test Movie",
        year=2026,
        confidence=1,
    )
    async with session_factory() as session:
        session.add(media)
        await session.flush()
        match = MetadataMatch(
            media_id=media.id,
            tmdb_id=metadata.tmdb_id,
            rank=1,
            score=1,
            match_reasons=["TMDB_ID_EXACT", "TITLE_EXACT", "YEAR_EXACT", "TYPE_EXACT"],
            conflicts=[],
            candidate_snapshot=metadata.model_dump(mode="json"),
        )
        session.add(match)
        await session.flush()
        review = IdentityReview(
            media_id=media.id,
            metadata_match_id=match.id,
            status="CONFIRMED",
            confirmed_by="test-operator",
            candidate_snapshot=metadata.model_dump(mode="json"),
        )
        session.add(review)
        await session.commit()
    return media, review


async def _publish_torrent_selection_mode(
    session: AsyncSession, mode: AutomationMode
) -> None:
    _, current = await get_current_policy(session)
    await publish_policy_revision(
        session,
        AutomationPolicyRevisionCreateRequest(
            base_revision_no=current.revision_no,
            identity_mode=AutomationMode.MANUAL,
            torrent_selection_mode=mode,
            approval_mode=AutomationMode.MANUAL,
            execution_mode=AutomationMode.MANUAL,
        ),
        actor="test-admin",
    )


@pytest.mark.asyncio
async def test_identity_confirmation_queues_readonly_search_in_manual_mode(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    media, review = await _seed_confirmed_media(session_factory)
    settings = _ready_pt_settings(automation_enabled=False)

    async with session_factory() as session:
        _, revision = await get_current_policy(session)
        assert revision.torrent_selection_mode == AutomationMode.MANUAL
        stored_media = await session.get(MediaItem, media.id)
        assert stored_media is not None

        job = await maybe_automate_torrent_search_after_identity(
            session,
            media=stored_media,
            trigger_created_at=review.created_at,
            settings=settings,
        )
        await session.commit()

        assert job is not None
        assert job.payload["read_only"] is True
        assert job.payload["site_id"] == "avistaz"
        assert "automation_policy_revision_id" not in job.payload
        assert "automation_decision_id" not in job.payload
        assert await session.scalar(select(func.count()).select_from(TorrentSearchRun)) == 1
        assert await session.scalar(select(func.count()).select_from(AutomationDecision)) == 0


@pytest.mark.asyncio
async def test_strict_identity_confirmation_queues_readonly_search(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _ready_pt_settings(automation_enabled=False)
    monkeypatch.setattr(processor_module, "get_settings", lambda: settings)
    now = datetime.now(UTC)
    media = MediaItem(
        source="nextfind",
        source_item_id="nextfind:strict-readonly-search",
        media_type=MediaType.MOVIE,
        tmdb_id=43,
        title="Strict Test Movie",
        year=2026,
        identity_confidence=IdentityConfidence.HIGH,
        metadata_status=MetadataStatus.UNRESOLVED,
        discovered_at=now,
        updated_at=now,
    )
    metadata = MetadataRecord(
        tmdb_id=43,
        imdb_id="tt0000043",
        media_type=MediaType.MOVIE,
        title="Strict Test Movie",
        english_title="Strict Test Movie",
        year=2026,
        confidence=1,
    )
    async with session_factory() as session:
        session.add(media)
        await session.commit()
        await enqueue_metadata_resolution(session, media, max_attempts=3)
        await session.commit()

    processor = JobProcessor(
        session_factory,
        "worker-strict-readonly-search",
        FakeMediaSource,
        lambda: MockTmdbProvider([metadata]),
    )
    assert await processor.run_once() is True

    async with session_factory() as session:
        review = await session.scalar(select(IdentityReview))
        search_run = await session.scalar(select(TorrentSearchRun))
        search_job = await session.scalar(
            select(Job).where(Job.job_type.startswith("TORRENT_SEARCH:"))
        )
        assert review is not None
        assert review.confirmed_by == "system:strict-identity"
        assert search_run is not None
        assert search_run.status == WorkflowStatus.PT_SEARCH_PENDING
        assert search_job is not None
        assert search_job.status == JobStatus.PENDING
        assert search_job.payload["read_only"] is True
        assert "automation_policy_revision_id" not in search_job.payload
        assert "automation_decision_id" not in search_job.payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settings",
    (
        Settings(
            _env_file=None,
            enable_avistaz_live_search=False,
            avistaz_username="test-user",
            avistaz_password="test-password",
            avistaz_pid="test-pid",
        ),
        Settings(_env_file=None, enable_avistaz_live_search=True),
    ),
)
async def test_identity_confirmation_does_not_queue_without_a_ready_pt_site(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    media, review = await _seed_confirmed_media(session_factory)

    async with session_factory() as session:
        stored_media = await session.get(MediaItem, media.id)
        assert stored_media is not None
        job = await maybe_automate_torrent_search_after_identity(
            session,
            media=stored_media,
            trigger_created_at=review.created_at,
            settings=settings,
        )
        await session.commit()

        assert job is None
        assert await session.scalar(select(func.count()).select_from(TorrentSearchRun)) == 0
        assert await session.scalar(select(func.count()).select_from(Job)) == 0


@pytest.mark.asyncio
async def test_disabled_torrent_selection_prevents_automatic_readonly_search(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    media, review = await _seed_confirmed_media(session_factory)

    async with session_factory() as session:
        await _publish_torrent_selection_mode(session, AutomationMode.DISABLED)
        stored_media = await session.get(MediaItem, media.id)
        assert stored_media is not None
        job = await maybe_automate_torrent_search_after_identity(
            session,
            media=stored_media,
            trigger_created_at=review.created_at,
            settings=_ready_pt_settings(),
        )
        await session.commit()

        assert job is None
        assert await session.scalar(select(func.count()).select_from(TorrentSearchRun)) == 0
        assert await session.scalar(select(func.count()).select_from(Job)) == 0


@pytest.mark.asyncio
async def test_search_completion_stops_at_candidates_even_when_automation_is_enabled(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media, review = await _seed_confirmed_media(session_factory)
    settings = _ready_pt_settings(automation_enabled=True)
    monkeypatch.setattr(processor_module, "get_settings", lambda: settings)
    lease_token = "simplified-readonly-search-lease"

    async with session_factory() as session:
        await _publish_torrent_selection_mode(session, AutomationMode.AUTO_IF_ELIGIBLE)
        stored_media = await session.get(MediaItem, media.id)
        assert stored_media is not None
        job = await maybe_automate_torrent_search_after_identity(
            session,
            media=stored_media,
            trigger_created_at=review.created_at,
            settings=settings,
        )
        assert job is not None
        job.status = JobStatus.RUNNING
        job.locked_by = "worker-simplified-readonly-search"
        job.locked_at = datetime.now(UTC)
        job.lease_token = lease_token
        await session.commit()
        search_run_id = str(job.payload["search_run_id"])

    candidate = TorrentCandidate(
        site_id="avistaz",
        torrent_id="simplified-candidate",
        release_title="Test Movie 2026 1080p WEB-DL",
        details_ref="avistaz:details:simplified-candidate",
        media_type=MediaType.MOVIE,
        tmdb_id=42,
        imdb_id="tt0000042",
        year=2026,
        resolution="1080p",
        source="WEB-DL",
        size_bytes=1_000_000_000,
        seeders=10,
        hit_and_run=False,
        info_hash="a" * 40,
        match_score=1,
        match_reasons=["TMDB_ID_EXACT"],
    )
    processor = JobProcessor(
        session_factory,
        "worker-simplified-readonly-search",
        FakeMediaSource,
    )
    await processor._complete_torrent_search(
        job.id,
        search_run_id,
        media.id,
        [candidate],
        [{"strategy": "TMDB_ID", "candidate_count": 1}],
        lease_token=lease_token,
    )

    async with session_factory() as session:
        run = await session.get(TorrentSearchRun, search_run_id)
        assert run is not None
        assert run.status == WorkflowStatus.TORRENT_REVIEW
        assert await session.scalar(select(func.count()).select_from(TorrentCandidateRecord)) == 1
        assert await session.scalar(select(func.count()).select_from(ApprovalRequest)) == 0
        assert await session.scalar(select(func.count()).select_from(AutomationDecision)) == 0
