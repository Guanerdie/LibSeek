from __future__ import annotations

import hashlib
from typing import cast

import bencodepy  # type: ignore[import-untyped]
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import MediaSourceAdapter, MetadataProvider, PtSiteAdapter
from app.adapters.downloaders.qbittorrent import QbittorrentAdapter
from app.adapters.pt_sites.avistaz import AvistaZMockAdapter
from app.core.config import Settings
from app.errors import AppError
from app.models.enums import IdentityConfidence, MediaType, MetadataStatus
from app.schemas.adapters import (
    AdapterManifest,
    LibraryDetails,
    MediaDiscoveryResult,
    MediaItemData,
    MetadataRecord,
    ProbeResult,
    TorrentCandidate,
)
from app.schemas.qbittorrent import QbTorrent
from app.simple.integrations import (
    identify_media,
    run_release_search,
    submit_download,
    sync_download_statuses,
    sync_nextfind,
)
from app.simple.models import (
    Download,
    DownloadState,
    Episode,
    LibraryMediaItem,
    MediaState,
    ReleaseCandidate,
)
from app.simple.service import complete_search, create_search, queue_download


def torrent_fixture() -> tuple[bytes, str]:
    info = {
        b"length": 20_000,
        b"name": b"Example.mkv",
        b"piece length": 16_384,
        b"pieces": b"p" * 40,
    }
    payload = bencodepy.encode({b"info": info})
    return payload, hashlib.sha1(bencodepy.encode(info)).hexdigest()


async def add_candidate(
    session: AsyncSession,
    *,
    source_item_id: str,
    warnings: list[str] | None = None,
    info_hash: str | None = None,
) -> tuple[LibraryMediaItem, ReleaseCandidate]:
    media = LibraryMediaItem(
        source_item_id=source_item_id,
        media_type=MediaType.MOVIE,
        tmdb_id=900,
        title="Example Movie",
        state=MediaState.READY,
    )
    session.add(media)
    await session.commit()
    await session.refresh(media)
    search = await create_search(session, media_id=media.id, site_ids=["avistaz"])
    await complete_search(
        session,
        search_id=search.id,
        candidates=[
            ReleaseCandidate(
                search_id=search.id,
                site_id="avistaz",
                torrent_id=f"torrent-{source_item_id}",
                title="Example.Movie.1080p.WEB-DL",
                score=0.9,
                reasons=[],
                warnings=warnings or [],
                info_hash=info_hash,
            )
        ],
    )
    candidate = await session.scalar(
        select(ReleaseCandidate).where(ReleaseCandidate.search_id == search.id)
    )
    assert candidate is not None
    return media, candidate


class FakeTorrentSource:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    async def fetch_torrent(self, _torrent_id: str) -> bytes:
        return self.payload

    async def aclose(self) -> None:
        return None


class FailingQb:
    async def authenticate(self) -> None:
        return None

    async def add_torrent(self, _payload: bytes, **_kwargs: object) -> None:
        raise AppError("QB_ADD_FAILED", "qBittorrent 拒绝了提交", status_code=502)

    async def aclose(self) -> None:
        return None


class StatusQb:
    def __init__(self, torrents: list[QbTorrent]) -> None:
        self.torrents = torrents

    async def authenticate(self) -> None:
        return None

    async def list_torrents(self) -> list[QbTorrent]:
        return self.torrents


class FakeNextFind(MediaSourceAdapter):
    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            id="nextfind",
            name="NextFind",
            adapter_type="media_source",
            version="test",
            enabled=True,
            mode="MOCK",
            description="test",
        )

    async def probe(self) -> ProbeResult:
        return ProbeResult(healthy=True, message="ok")

    async def authenticate(self) -> None:
        return None

    async def list_missing_media(self) -> MediaDiscoveryResult:
        from app.core.time import utc_now

        now = utc_now()
        return MediaDiscoveryResult(
            items=[
                MediaItemData(
                    source="nextfind",
                    source_item_id="source-tv-1",
                    media_type=MediaType.TV,
                    tmdb_id=300,
                    title="A Missing Show",
                    missing_episodes=["S01E02", "S01E03"],
                    identity_confidence=IdentityConfidence.HIGH,
                    metadata_status=MetadataStatus.RESOLVED,
                    discovered_at=now,
                    updated_at=now,
                )
            ]
        )

    async def get_library_details(
        self, media_type: MediaType, tmdb_id: int
    ) -> LibraryDetails:
        return LibraryDetails(tmdb_id=tmdb_id, media_type=media_type)


class FakeTmdb(MetadataProvider):
    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            id="tmdb",
            name="TMDB",
            adapter_type="metadata",
            version="test",
            enabled=True,
            mode="MOCK",
            description="test",
        )

    async def probe(self) -> ProbeResult:
        return ProbeResult(healthy=True, message="ok")

    async def get_by_tmdb_id(self, media_type: MediaType, tmdb_id: int) -> MetadataRecord:
        return MetadataRecord(
            tmdb_id=tmdb_id,
            media_type=media_type,
            title="A Missing Show",
            chinese_title="缺失剧集",
            original_title="A Missing Show",
            year=2026,
        )

    async def search(
        self, media_type: MediaType, title: str, year: int | None = None
    ) -> list[MetadataRecord]:
        del title, year
        return [await self.get_by_tmdb_id(media_type, 300)]

    async def get_external_ids(
        self, media_type: MediaType, tmdb_id: int
    ) -> dict[str, str]:
        del media_type, tmdb_id
        return {}

    async def get_country_codes(
        self, media_type: MediaType, tmdb_id: int
    ) -> list[str] | None:
        del media_type, tmdb_id
        return None

    async def get_tv_episode_matrix(self, tmdb_id: int) -> dict[int, list[int]] | None:
        del tmdb_id
        return None


@pytest.mark.asyncio
async def test_daily_flow_goes_from_media_to_candidate_to_download(session_factory) -> None:
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="nextfind-1",
            media_type=MediaType.TV,
            tmdb_id=100,
            title="Example Show",
            state=MediaState.READY,
        )
        session.add(media)
        await session.commit()
        await session.refresh(media)

        search = await create_search(session, media_id=media.id, site_ids=["avistaz"])
        await complete_search(
            session,
            search_id=search.id,
            candidates=[
                ReleaseCandidate(
                    search_id=search.id,
                    site_id="avistaz",
                    torrent_id="torrent-1",
                    title="Example.Show.S01.1080p.WEB-DL",
                    size_bytes=10_000,
                    seeders=12,
                    resolution="1080p",
                    source="WEB-DL",
                    codec="H.265",
                    season_coverage=[1],
                    episode_coverage=[],
                    score=0.91,
                    reasons=["匹配第一季", "分辨率符合偏好"],
                    warnings=[],
                    info_hash="a" * 40,
                )
            ],
        )

        candidate = await session.scalar(select(ReleaseCandidate))
        assert candidate is not None
        download = await queue_download(
            session, candidate_id=candidate.id, confirm_warnings=False
        )
        assert download.media_id == media.id
        assert download.info_hash == "a" * 40

        stored_media = await session.get(LibraryMediaItem, media.id)
        assert stored_media is not None
        assert stored_media.state == MediaState.DOWNLOADING
        assert await session.scalar(select(Download)) is not None


@pytest.mark.asyncio
async def test_candidate_warning_requires_one_explicit_confirmation(session_factory) -> None:
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="nextfind-2",
            media_type=MediaType.MOVIE,
            tmdb_id=200,
            title="Example Movie",
            state=MediaState.READY,
        )
        session.add(media)
        await session.commit()
        await session.refresh(media)
        search = await create_search(session, media_id=media.id, site_ids=["avistaz"])
        await complete_search(
            session,
            search_id=search.id,
            candidates=[
                ReleaseCandidate(
                    search_id=search.id,
                    site_id="avistaz",
                    torrent_id="torrent-warning",
                    title="Example.Movie.2160p.BluRay",
                    score=0.7,
                    reasons=[],
                    warnings=["候选大小超过日常偏好"],
                )
            ],
        )
        candidate = await session.scalar(select(ReleaseCandidate))
        assert candidate is not None

        with pytest.raises(AppError) as caught:
            await queue_download(
                session, candidate_id=candidate.id, confirm_warnings=False
            )
        assert caught.value.error_code == "CANDIDATE_CONFIRMATION_REQUIRED"

        download = await queue_download(
            session, candidate_id=candidate.id, confirm_warnings=True
        )
        repeated = await queue_download(
            session, candidate_id=candidate.id, confirm_warnings=True
        )
        assert repeated.id == download.id


@pytest.mark.asyncio
async def test_search_requires_confirmed_tmdb_identity(session_factory) -> None:
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="nextfind-3",
            media_type=MediaType.MOVIE,
            title="Unknown Movie",
        )
        session.add(media)
        await session.commit()
        await session.refresh(media)

        with pytest.raises(AppError) as caught:
            await create_search(session, media_id=media.id, site_ids=["avistaz"])
        assert caught.value.error_code == "MEDIA_IDENTITY_REQUIRED"


@pytest.mark.asyncio
async def test_existing_adapters_feed_the_simplified_domain(session_factory) -> None:
    async with session_factory() as session:
        created, updated = await sync_nextfind(session, FakeNextFind())
        assert (created, updated) == (1, 0)
        media = await session.scalar(select(LibraryMediaItem))
        assert media is not None
        episodes = list(await session.scalars(select(Episode)))
        assert [(item.season_number, item.episode_number) for item in episodes] == [
            (1, 2),
            (1, 3),
        ]

        identified = await identify_media(session, media.id, FakeTmdb())
        assert identified.title == "缺失剧集"
        search = await create_search(session, media_id=media.id, site_ids=["avistaz"])
        adapter = AvistaZMockAdapter(
            fixtures=[
                TorrentCandidate(
                    site_id="avistaz",
                    torrent_id="release-300",
                    release_title="A.Missing.Show.S01.1080p.WEB-DL",
                    details_ref="avistaz:details:release-300",
                    media_type=MediaType.TV,
                    tmdb_id=300,
                    season=1,
                    collection_type="season",
                    resolution="1080p",
                    source="WEB-DL",
                    size_bytes=1_000_000,
                    seeders=8,
                    hit_and_run=False,
                )
            ]
        )
        await run_release_search(session, search.id, lambda site_id: adapter)
        candidate = await session.scalar(select(ReleaseCandidate))
        assert candidate is not None
        assert candidate.score > 0.5
        assert candidate.season_coverage == [1]


@pytest.mark.asyncio
async def test_submit_checks_candidate_warning_before_creating_external_adapters(
    session_factory,
) -> None:
    async with session_factory() as session:
        _, candidate = await add_candidate(
            session,
            source_item_id="warning-before-adapters",
            warnings=["候选大小超过日常偏好"],
        )
        qb_factory_calls = 0

        def unused_qb_factory() -> QbittorrentAdapter:
            nonlocal qb_factory_calls
            qb_factory_calls += 1
            raise AssertionError("风险提示确认前不应创建 qBittorrent 连接")

        def unused_pt_factory(_site_id: str) -> PtSiteAdapter:
            raise AssertionError("风险提示确认前不应创建 PT 连接")

        with pytest.raises(AppError) as caught:
            await submit_download(
                session,
                candidate_id=candidate.id,
                confirm_warnings=False,
                pt_factory=unused_pt_factory,
                qb_factory=unused_qb_factory,
                settings=Settings(
                    _env_file=None,
                    qb_target_category="unin",
                    qb_target_save_path="/downloads",
                ),
            )

        assert caught.value.error_code == "CANDIDATE_CONFIRMATION_REQUIRED"
        assert qb_factory_calls == 0
        assert await session.scalar(select(Download)) is None


@pytest.mark.asyncio
async def test_known_qb_submission_failure_marks_media_for_attention(session_factory) -> None:
    payload, info_hash = torrent_fixture()
    async with session_factory() as session:
        media, candidate = await add_candidate(
            session,
            source_item_id="known-qb-failure",
            info_hash=info_hash,
        )
        pt = cast(PtSiteAdapter, FakeTorrentSource(payload))
        qb = cast(QbittorrentAdapter, FailingQb())

        with pytest.raises(AppError) as caught:
            await submit_download(
                session,
                candidate_id=candidate.id,
                confirm_warnings=False,
                pt_factory=lambda _site_id: pt,
                qb_factory=lambda: qb,
                settings=Settings(
                    _env_file=None,
                    qb_target_category="unin",
                    qb_target_save_path="/downloads",
                ),
            )

        assert caught.value.error_code == "QB_ADD_FAILED"
        download = await session.scalar(select(Download))
        assert download is not None
        assert download.state == DownloadState.ERROR
        assert download.error_message == "qBittorrent 拒绝了提交"
        await session.refresh(media)
        assert media.state == MediaState.NEEDS_ATTENTION
        assert media.attention_reason == "qBittorrent 拒绝了提交"


@pytest.mark.asyncio
async def test_unknown_submission_outcome_recovers_from_qb_status(session_factory) -> None:
    _, info_hash = torrent_fixture()
    async with session_factory() as session:
        media, candidate = await add_candidate(
            session,
            source_item_id="unknown-outcome",
            info_hash=info_hash,
        )
        download = await queue_download(
            session, candidate_id=candidate.id, confirm_warnings=False
        )
        download.state = DownloadState.OUTCOME_UNKNOWN
        download.error_message = "提交结果未知"
        await session.commit()

        updated = await sync_download_statuses(
            session,
            cast(
                QbittorrentAdapter,
                StatusQb(
                    [
                        QbTorrent(
                            hash=info_hash,
                            name="Example.mkv",
                            size=20_000,
                            progress=0.5,
                            ratio=0,
                            state="downloading",
                        )
                    ]
                ),
            ),
        )

        assert updated == 1
        await session.refresh(download)
        await session.refresh(media)
        assert download.state == DownloadState.DOWNLOADING
        assert download.error_message is None
        assert media.state == MediaState.DOWNLOADING
        assert media.attention_reason is None


@pytest.mark.asyncio
async def test_completed_seeding_torrent_marks_media_complete(session_factory) -> None:
    _, info_hash = torrent_fixture()
    async with session_factory() as session:
        media, candidate = await add_candidate(
            session,
            source_item_id="completed-seeding",
            info_hash=info_hash,
        )
        download = await queue_download(
            session, candidate_id=candidate.id, confirm_warnings=False
        )

        updated = await sync_download_statuses(
            session,
            cast(
                QbittorrentAdapter,
                StatusQb(
                    [
                        QbTorrent(
                            hash=info_hash,
                            name="Example.mkv",
                            size=20_000,
                            progress=1,
                            ratio=1.5,
                            state="uploading",
                        )
                    ]
                ),
            ),
        )

        assert updated == 1
        await session.refresh(download)
        await session.refresh(media)
        assert download.state == DownloadState.SEEDING
        assert media.state == MediaState.COMPLETE
        assert media.attention_reason is None
