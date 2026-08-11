from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, cast

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import sanitize_details
from app.errors import AppError
from app.models.entities import (
    ApprovalEvent,
    ApprovalRequest,
    DownloadExecution,
    DownloadExecutionEvent,
    DownloadJob,
    DownloadJobEvent,
    DownloadPlan,
    MediaItem,
)
from app.models.enums import DownloadJobStatus, HnrStatus
from app.schemas.approvals import MediaDestinationPlan
from app.schemas.jobs import (
    DownloadJobResponse,
    DownloadJobSummaryApprovalResponse,
    DownloadJobSummaryExecutionResponse,
    DownloadJobSummaryMediaResponse,
    DownloadJobSummaryResponse,
    DownloadJobTimelineItemResponse,
    DownloadJobTimelineResponse,
)
from app.services.approvals import verify_download_plan, verify_snapshot
from app.services.executions import requires_reconciliation, verify_download_execution


async def list_download_jobs(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    status: DownloadJobStatus | None = None,
) -> tuple[list[DownloadJob], int]:
    filters = []
    if status is not None:
        filters.append(DownloadJob.status == status)
    total = (
        await session.scalar(select(func.count()).select_from(DownloadJob).where(*filters))
        or 0
    )
    statement = (
        select(DownloadJob)
        .where(*filters)
        .order_by(DownloadJob.created_at.desc(), DownloadJob.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list((await session.scalars(statement)).all()), total


async def get_download_job(session: AsyncSession, job_id: str) -> DownloadJob:
    job = await session.get(DownloadJob, job_id)
    if job is None:
        raise AppError("DOWNLOAD_JOB_NOT_FOUND", "下载任务不存在", status_code=404)
    return job


async def list_download_job_timeline(
    session: AsyncSession, job_id: str
) -> DownloadJobTimelineResponse:
    job = await get_download_job(session, job_id)
    approval_events = list(
        (
            await session.scalars(
                select(ApprovalEvent).where(
                    ApprovalEvent.approval_request_id == job.approval_id
                )
            )
        ).all()
    )
    execution_events = list(
        (
            await session.scalars(
                select(DownloadExecutionEvent).where(
                    DownloadExecutionEvent.download_execution_id == job.execution_id
                )
            )
        ).all()
    )
    job_events = list(
        (
            await session.scalars(
                select(DownloadJobEvent).where(DownloadJobEvent.download_job_id == job.id)
            )
        ).all()
    )

    rows: list[
        tuple[datetime, int, str, DownloadJobTimelineItemResponse]
    ] = []
    for approval_event in approval_events:
        rows.append(
            _timeline_row(
                source="approval",
                source_order=0,
                event_id=approval_event.id,
                event_type=approval_event.event_type,
                from_status=approval_event.from_status,
                to_status=approval_event.to_status,
                actor=approval_event.actor,
                details=approval_event.sanitized_details,
                created_at=approval_event.created_at,
            )
        )
    for execution_event in execution_events:
        rows.append(
            _timeline_row(
                source="execution",
                source_order=1,
                event_id=execution_event.id,
                event_type=execution_event.event_type,
                from_status=execution_event.from_status,
                to_status=execution_event.to_status,
                actor=execution_event.actor,
                details=execution_event.sanitized_details,
                created_at=execution_event.created_at,
            )
        )
    for job_event in job_events:
        rows.append(
            _timeline_row(
                source="job",
                source_order=2,
                event_id=job_event.id,
                event_type=job_event.event_type,
                from_status=job_event.from_status,
                to_status=job_event.to_status,
                actor=job_event.actor,
                details=job_event.sanitized_details,
                created_at=job_event.created_at,
            )
        )
    rows.sort(key=lambda row: (_as_utc(row[0]), row[1], row[2]))
    return DownloadJobTimelineResponse(job_id=job.id, items=[row[3] for row in rows])


async def get_download_job_summary(
    session: AsyncSession, job_id: str
) -> DownloadJobSummaryResponse:
    job = await get_download_job(session, job_id)
    media = await session.get(MediaItem, job.media_item_id)
    approval = await session.get(ApprovalRequest, job.approval_id)
    execution = await session.get(DownloadExecution, job.execution_id)
    plan = await session.scalar(
        select(DownloadPlan).where(DownloadPlan.approval_id == job.approval_id).limit(1)
    )
    if media is None or approval is None or execution is None or plan is None:
        raise AppError(
            "DOWNLOAD_JOB_BINDING_INVALID",
            "下载任务缺少影视、审批、执行或下载计划绑定",
            status_code=409,
        )
    snapshot = verify_snapshot(approval)
    verify_download_plan(plan, approval)
    await verify_download_execution(session, execution)
    if (
        approval.media_item_id != job.media_item_id
        or execution.approval_id != job.approval_id
        or plan.approval_id != job.approval_id
        or snapshot.media_item_id != job.media_item_id
    ):
        raise AppError(
            "DOWNLOAD_JOB_BINDING_INVALID",
            "下载任务与影视、审批、执行或下载计划绑定不一致",
            status_code=409,
        )
    try:
        destination = MediaDestinationPlan.model_validate(plan.media_destination_plan)
    except ValidationError as exc:
        raise AppError(
            "DOWNLOAD_JOB_BINDING_INVALID",
            "下载任务的影视目标计划无效",
            status_code=409,
        ) from exc
    if (
        destination.media_item_id != snapshot.media_item_id
        or destination.media_type != snapshot.media_type
        or destination.tmdb_id != snapshot.tmdb_id
        or destination.season != snapshot.season
        or destination.episodes != snapshot.episodes
    ):
        raise AppError(
            "DOWNLOAD_JOB_BINDING_INVALID",
            "下载任务与影视目标计划绑定不一致",
            status_code=409,
        )
    warnings = list(dict.fromkeys(plan.warnings))
    if job.hnr_status == HnrStatus.UNKNOWN and "HNR_STATUS_UNKNOWN" not in warnings:
        warnings.append("HNR_STATUS_UNKNOWN")
    return DownloadJobSummaryResponse(
        job=DownloadJobResponse.model_validate(job),
        media=DownloadJobSummaryMediaResponse(
            id=snapshot.media_item_id,
            title=snapshot.media_title,
            media_type=snapshot.media_type,
            tmdb_id=snapshot.tmdb_id,
            year=snapshot.year,
            season=destination.season,
            episodes=(
                list(destination.episodes) if destination.episodes is not None else None
            ),
        ),
        approval=DownloadJobSummaryApprovalResponse(
            id=approval.id,
            status=approval.status,
            snapshot_hash=approval.snapshot_hash,
            expires_at=approval.expires_at,
        ),
        execution=DownloadJobSummaryExecutionResponse(
            id=execution.id,
            status=execution.status,
            launch_mode=execution.launch_mode,
            requires_reconciliation=requires_reconciliation(execution),
            actual_info_hash_v1=execution.actual_info_hash_v1,
            actual_info_hash_v2=execution.actual_info_hash_v2,
            validated_at=execution.validated_at,
            submitted_at=execution.submitted_at,
            verified_at=execution.verified_at,
        ),
        warnings=warnings,
    )


def _timeline_row(
    *,
    source: Literal["approval", "execution", "job"],
    source_order: int,
    event_id: str,
    event_type: str,
    from_status: str | None,
    to_status: str,
    actor: str,
    details: dict[str, object],
    created_at: datetime,
) -> tuple[datetime, int, str, DownloadJobTimelineItemResponse]:
    response = DownloadJobTimelineItemResponse(
        source=source,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        actor=actor,
        sanitized_details=cast(dict[str, object], sanitize_details(details)),
        created_at=created_at,
    )
    return created_at, source_order, event_id, response


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
