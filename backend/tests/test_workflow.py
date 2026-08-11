from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.metadata.mock_tmdb import MockTmdbProvider
from app.adapters.pt_sites.avistaz import AvistaZMockAdapter
from app.errors import AppError
from app.models.entities import (
    IdentityReview,
    MediaItem,
    MetadataMatch,
    TorrentCandidateRecord,
    TorrentSearchRun,
)
from app.models.enums import (
    IdentityConfidence,
    MediaType,
    MetadataStatus,
    WorkflowStatus,
)
from app.schemas.adapters import MetadataRecord, TorrentCandidate
from app.schemas.entities import IdentityConfirmationRequest, TorrentSearchCreateRequest
from app.services.workflow import (
    confirm_identity,
    enqueue_metadata_resolution,
    enqueue_torrent_search,
)
from app.workers.processor import JobProcessor
from tests.test_discovery_worker import FakeMediaSource


def record(
    tmdb_id: int,
    *,
    media_type: MediaType = MediaType.MOVIE,
    episode_matrix: dict[int, list[int]] | None = None,
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
        episode_matrix=episode_matrix or {},
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
        assert refreshed is not None and refreshed.workflow_status == WorkflowStatus.IDENTITY_REVIEW
        assert match is not None and "TMDB_ID_EXACT" in match.match_reasons


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
        match = await session.scalar(select(MetadataMatch))
        assert match is not None
        assert "MEDIA_TYPE_CONFLICT" in match.conflicts


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
        assert refreshed is not None and refreshed.tmdb_id is None
        assert refreshed.workflow_status == WorkflowStatus.IDENTITY_REVIEW
        assert count == 2


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
                preferred_resolutions=["1080p"], preferred_subtitles=["Chinese"]
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
    pt = AvistaZMockAdapter([fixture], manifest_id="avistaz")
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
