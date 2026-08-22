from __future__ import annotations

from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.pt_sites.catalog import build_pt_site_catalog
from app.core.config import Settings
from app.errors import AppError
from app.models.entities import (
    ApprovalRequest,
    DownloadBatch,
    DownloadBatchItem,
    DownloadExecution,
    DownloadJob,
    IdentityReview,
    Job,
    MediaItem,
    TorrentSearchRun,
)
from app.models.enums import (
    ApprovalStatus,
    DownloadBatchItemStatus,
    DownloadBatchMode,
    DownloadBatchStatus,
    DownloadExecutionStatus,
    DownloadJobStatus,
    JobStatus,
    WorkflowStatus,
)
from app.schemas.download_batches import (
    DownloadBatchCreateRequest,
    DownloadBatchItemResponse,
    DownloadBatchResponse,
    DownloadBatchSummaryResponse,
)
from app.schemas.entities import TorrentSearchCreateRequest
from app.services.workflow import enqueue_metadata_resolution, enqueue_torrent_search


async def create_download_batch(
    session: AsyncSession,
    request: DownloadBatchCreateRequest,
    settings: Settings,
    *,
    actor: str,
) -> DownloadBatch:
    media = list(
        (await session.scalars(select(MediaItem).where(MediaItem.id.in_(request.media_ids)))).all()
    )
    if len(media) != len(request.media_ids):
        raise AppError("BATCH_MEDIA_NOT_FOUND", "部分影视条目不存在", status_code=404)
    if any(item.discovery_status != "MISSING" for item in media):
        raise AppError(
            "BATCH_MEDIA_NOT_MISSING",
            "下载批次只能包含当前仍未入库的影视",
            status_code=409,
        )
    catalog = build_pt_site_catalog(settings)
    for item in media:
        catalog.require_searchable(request.site_id, media_type=item.media_type)

    preferences = {
        "preferred_resolutions": request.preferred_resolutions,
        "preferred_sources": request.preferred_sources,
        "preferred_audio": request.preferred_audio,
        "preferred_subtitles": request.preferred_subtitles,
        "max_size_bytes": request.max_candidate_size_bytes,
    }
    batch = DownloadBatch(
        name=request.name.strip(),
        mode=request.mode,
        launch_mode=request.launch_mode,
        site_id=request.site_id,
        preferences=preferences,
        max_total_size_bytes=request.max_total_size_bytes,
        max_items=len(media),
        created_by=actor,
    )
    session.add(batch)
    await session.flush()
    by_id = {item.id: item for item in media}
    for media_id in request.media_ids:
        batch_item = DownloadBatchItem(batch_id=batch.id, media_item_id=media_id)
        session.add(batch_item)
        await _enqueue_next_step(session, batch, batch_item, by_id[media_id], settings)
    await session.flush()
    return batch


async def _enqueue_next_step(
    session: AsyncSession,
    batch: DownloadBatch,
    item: DownloadBatchItem,
    media: MediaItem,
    settings: Settings,
) -> None:
    review = await session.scalar(
        select(IdentityReview.id).where(
            IdentityReview.media_id == media.id, IdentityReview.status == "CONFIRMED"
        )
    )
    try:
        if review is None:
            job, _ = await enqueue_metadata_resolution(
                session, media, max_attempts=settings.job_max_attempts
            )
            job.payload = {
                **job.payload,
                "download_batch_id": batch.id,
                "download_batch_mode": batch.mode.value,
                "download_batch_site_id": batch.site_id,
                "download_batch_preferences": batch.preferences,
                "download_batch_launch_mode": batch.launch_mode.value,
            }
            item.status = DownloadBatchItemStatus.RESOLVING_IDENTITY
            return
        search_request = TorrentSearchCreateRequest(site_id=batch.site_id, **batch.preferences)
        _run, job, _ = await enqueue_torrent_search(
            session,
            media,
            search_request,
            max_attempts=settings.job_max_attempts,
            settings=settings,
        )
        job.payload = {
            **job.payload,
            "download_batch_id": batch.id,
            "download_batch_mode": batch.mode.value,
            "download_batch_launch_mode": batch.launch_mode.value,
        }
        item.status = DownloadBatchItemStatus.SEARCHING
    except AppError as exc:
        if exc.status_code == 409:
            item.status = DownloadBatchItemStatus.MANUAL_REQUIRED
            item.error_code = exc.error_code
            item.error_message = exc.message
            return
        raise


async def refresh_download_batch(session: AsyncSession, batch: DownloadBatch) -> None:
    items = list(
        (
            await session.scalars(
                select(DownloadBatchItem).where(DownloadBatchItem.batch_id == batch.id)
            )
        ).all()
    )
    for item in items:
        item.status, item.error_code, item.error_message = await _derive_item_status(
            session, batch, item.media_item_id
        )
    statuses = {item.status for item in items}
    if statuses and statuses <= {DownloadBatchItemStatus.COMPLETED}:
        batch.status = DownloadBatchStatus.COMPLETED
    elif statuses & {DownloadBatchItemStatus.MANUAL_REQUIRED, DownloadBatchItemStatus.FAILED}:
        batch.status = DownloadBatchStatus.NEEDS_ATTENTION
    else:
        batch.status = DownloadBatchStatus.ACTIVE
    await session.flush()


async def _derive_item_status(
    session: AsyncSession, batch: DownloadBatch, media_id: str
) -> tuple[DownloadBatchItemStatus, str | None, str | None]:
    job = await session.scalar(
        select(DownloadJob)
        .where(DownloadJob.media_item_id == media_id)
        .order_by(DownloadJob.created_at.desc())
        .limit(1)
    )
    if job is not None:
        if job.status in {DownloadJobStatus.COMPLETED, DownloadJobStatus.SEEDING}:
            return DownloadBatchItemStatus.COMPLETED, None, None
        if job.status in {DownloadJobStatus.ERROR, DownloadJobStatus.MISSING}:
            return DownloadBatchItemStatus.FAILED, job.error_code, job.error_message
        if job.status == DownloadJobStatus.DOWNLOADING:
            return DownloadBatchItemStatus.DOWNLOADING, None, None
        return DownloadBatchItemStatus.SUBMITTED, None, None

    execution = await session.scalar(
        select(DownloadExecution)
        .join(ApprovalRequest, ApprovalRequest.id == DownloadExecution.approval_id)
        .where(ApprovalRequest.media_item_id == media_id)
        .order_by(DownloadExecution.created_at.desc())
        .limit(1)
    )
    if execution is not None:
        if execution.status in {DownloadExecutionStatus.FAILED, DownloadExecutionStatus.CANCELLED}:
            return DownloadBatchItemStatus.FAILED, execution.error_code, execution.error_message
        if execution.status in {
            DownloadExecutionStatus.OUTCOME_UNKNOWN,
            DownloadExecutionStatus.RECONCILIATION_REQUIRED,
            DownloadExecutionStatus.RECONCILIATION_PENDING,
        }:
            return (
                DownloadBatchItemStatus.MANUAL_REQUIRED,
                "RECONCILIATION_REQUIRED",
                "下载提交结果需要人工对账",
            )
        if execution.status in {
            DownloadExecutionStatus.SUBMITTED,
            DownloadExecutionStatus.ALREADY_PRESENT,
        }:
            return DownloadBatchItemStatus.SUBMITTED, None, None
        return DownloadBatchItemStatus.QUEUED, None, None

    approval = await session.scalar(
        select(ApprovalRequest)
        .where(ApprovalRequest.media_item_id == media_id)
        .order_by(ApprovalRequest.created_at.desc())
        .limit(1)
    )
    if approval is not None:
        if approval.status in {
            ApprovalStatus.REJECTED,
            ApprovalStatus.REVOKED,
            ApprovalStatus.EXPIRED,
        }:
            return (
                DownloadBatchItemStatus.MANUAL_REQUIRED,
                f"APPROVAL_{approval.status.value}",
                "审批未能继续",
            )
        return DownloadBatchItemStatus.APPROVING, None, None

    run = await session.scalar(
        select(TorrentSearchRun)
        .where(TorrentSearchRun.media_id == media_id, TorrentSearchRun.site_id == batch.site_id)
        .order_by(TorrentSearchRun.created_at.desc())
        .limit(1)
    )
    if run is not None:
        if run.status == WorkflowStatus.TORRENT_REVIEW:
            if batch.mode == DownloadBatchMode.SEARCH_ONLY:
                return DownloadBatchItemStatus.COMPLETED, None, None
            return (
                DownloadBatchItemStatus.MANUAL_REQUIRED,
                "AUTO_SELECTION_NOT_COMPLETED",
                "没有候选通过自动下载规则",
            )
        if run.status == WorkflowStatus.NO_CANDIDATE:
            return DownloadBatchItemStatus.MANUAL_REQUIRED, "NO_CANDIDATE", "没有找到符合条件的候选"
        if run.status == WorkflowStatus.SEARCH_FAILED:
            return DownloadBatchItemStatus.FAILED, run.error_code, run.error_message
        return DownloadBatchItemStatus.SEARCHING, None, None

    media = await session.get(MediaItem, media_id)
    if media is None:
        return DownloadBatchItemStatus.FAILED, "MEDIA_NOT_FOUND", "影视条目不存在"
    if media.workflow_status == WorkflowStatus.IDENTITY_REVIEW:
        return (
            DownloadBatchItemStatus.MANUAL_REQUIRED,
            "IDENTITY_CONFIRMATION_REQUIRED",
            "影视身份需要人工确认",
        )
    failed_job = await session.scalar(
        select(Job)
        .where(Job.payload["media_id"].as_string() == media_id, Job.status == JobStatus.FAILED)
        .order_by(Job.updated_at.desc())
        .limit(1)
    )
    if failed_job is not None:
        return DownloadBatchItemStatus.FAILED, failed_job.error_code, failed_job.error_message
    return DownloadBatchItemStatus.RESOLVING_IDENTITY, None, None


async def retry_download_batch_item(
    session: AsyncSession, batch: DownloadBatch, item: DownloadBatchItem, settings: Settings
) -> None:
    media = await session.get(MediaItem, item.media_item_id)
    if media is None:
        raise AppError("MEDIA_NOT_FOUND", "影视条目不存在", status_code=404)
    item.error_code = None
    item.error_message = None
    await _enqueue_next_step(session, batch, item, media, settings)


async def get_download_batch(session: AsyncSession, batch_id: str) -> DownloadBatch:
    batch = await session.get(DownloadBatch, batch_id)
    if batch is None:
        raise AppError("DOWNLOAD_BATCH_NOT_FOUND", "下载批次不存在", status_code=404)
    await refresh_download_batch(session, batch)
    return batch


async def download_batch_response(
    session: AsyncSession, batch: DownloadBatch
) -> DownloadBatchResponse:
    rows = (
        await session.execute(
            select(DownloadBatchItem, MediaItem)
            .join(MediaItem, MediaItem.id == DownloadBatchItem.media_item_id)
            .where(DownloadBatchItem.batch_id == batch.id)
            .order_by(DownloadBatchItem.created_at, DownloadBatchItem.id)
        )
    ).all()
    counts = Counter(item.status.value for item, _media in rows)
    return DownloadBatchResponse(
        id=batch.id,
        name=batch.name,
        mode=batch.mode,
        launch_mode=batch.launch_mode,
        status=batch.status,
        site_id=batch.site_id,
        preferences=batch.preferences,
        max_total_size_bytes=batch.max_total_size_bytes,
        max_items=batch.max_items,
        created_by=batch.created_by,
        created_at=batch.created_at,
        updated_at=batch.updated_at,
        counts=dict(counts),
        items=[
            DownloadBatchItemResponse(
                id=item.id,
                media_item_id=media.id,
                title=media.title,
                media_type=media.media_type.value,
                year=media.year,
                status=item.status,
                error_code=item.error_code,
                error_message=item.error_message,
                updated_at=item.updated_at,
            )
            for item, media in rows
        ],
    )


async def list_download_batches(
    session: AsyncSession, *, page: int, page_size: int
) -> tuple[list[DownloadBatchSummaryResponse], int]:
    total = int(await session.scalar(select(func.count()).select_from(DownloadBatch)) or 0)
    batches = list(
        (
            await session.scalars(
                select(DownloadBatch)
                .order_by(DownloadBatch.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    )
    result: list[DownloadBatchSummaryResponse] = []
    for batch in batches:
        await refresh_download_batch(session, batch)
        counts = Counter(
            (
                await session.scalars(
                    select(DownloadBatchItem.status).where(DownloadBatchItem.batch_id == batch.id)
                )
            ).all()
        )
        result.append(
            DownloadBatchSummaryResponse(
                id=batch.id,
                name=batch.name,
                mode=batch.mode,
                launch_mode=batch.launch_mode,
                status=batch.status,
                site_id=batch.site_id,
                max_items=batch.max_items,
                created_by=batch.created_by,
                created_at=batch.created_at,
                updated_at=batch.updated_at,
                counts={key.value: value for key, value in counts.items()},
            )
        )
    return result, total
