from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models.entities import DownloadBatchItem, Job, MediaItem
from app.models.enums import (
    DownloadBatchItemStatus,
    DownloadBatchMode,
    DownloadLaunchMode,
    IdentityConfidence,
    MediaType,
    MetadataStatus,
)
from app.schemas.download_batches import DownloadBatchCreateRequest
from app.services.download_batches import create_download_batch, download_batch_response


def media_item(source_id: str) -> MediaItem:
    now = datetime.now(UTC)
    return MediaItem(
        source="nextfind",
        source_item_id=source_id,
        media_type=MediaType.MOVIE,
        tmdb_id=None,
        title=f"测试电影 {source_id}",
        year=2026,
        identity_confidence=IdentityConfidence.NEEDS_CONFIRMATION,
        metadata_status=MetadataStatus.UNRESOLVED,
        discovered_at=now,
        updated_at=now,
    )


def batch_settings() -> Settings:
    return Settings(
        enable_avistaz_live_search=True,
        pt_site_runtime_configured=True,
    )


@pytest.mark.asyncio
async def test_create_batch_groups_items_and_queues_identity_resolution(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first = media_item("batch-1")
    second = media_item("batch-2")
    async with session_factory() as session:
        session.add_all([first, second])
        await session.commit()
        batch = await create_download_batch(
            session,
            DownloadBatchCreateRequest(
                name="周末批次",
                media_ids=[first.id, second.id],
                mode=DownloadBatchMode.AUTO_SAFE,
                site_id="avistaz",
                preferred_resolutions=["1080p"],
            ),
            batch_settings(),
            actor="admin",
        )
        await session.commit()

        items = list(
            (
                await session.scalars(
                    select(DownloadBatchItem).where(DownloadBatchItem.batch_id == batch.id)
                )
            ).all()
        )
        jobs = list(
            (
                await session.scalars(select(Job).where(Job.job_type.like("RESOLVE_METADATA:%")))
            ).all()
        )
        response = await download_batch_response(session, batch)

    assert len(items) == 2
    assert {item.status for item in items} == {DownloadBatchItemStatus.RESOLVING_IDENTITY}
    assert len(jobs) == 2
    assert response.counts == {"RESOLVING_IDENTITY": 2}
    assert response.preferences["preferred_resolutions"] == ["1080p"]
    assert response.launch_mode == DownloadLaunchMode.SCHEDULED_START


def test_batch_request_rejects_duplicate_media_ids() -> None:
    with pytest.raises(ValueError, match="media_ids must be unique"):
        DownloadBatchCreateRequest(
            name="重复",
            media_ids=["same", "same"],
            site_id="avistaz",
        )


def test_search_only_batch_never_starts_downloads() -> None:
    request = DownloadBatchCreateRequest(
        name="只搜索",
        media_ids=["media-1"],
        mode=DownloadBatchMode.SEARCH_ONLY,
        launch_mode=DownloadLaunchMode.SCHEDULED_START,
        site_id="avistaz",
    )

    assert request.launch_mode == DownloadLaunchMode.ADD_PAUSED
