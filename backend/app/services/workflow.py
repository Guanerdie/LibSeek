from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.episodes import derive_missing_episode_codes
from app.core.security import sanitize_details
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
from app.schemas.adapters import MetadataRecord
from app.schemas.entities import IdentityConfirmationRequest, TorrentSearchCreateRequest

ACTIVE_JOB_STATUSES = (JobStatus.PENDING, JobStatus.RUNNING, JobStatus.RETRY_WAIT)
ACTIVE_SEARCH_STATUSES = (WorkflowStatus.PT_SEARCH_PENDING, WorkflowStatus.PT_SEARCHING)
SEARCH_STATUS_PRECEDENCE = (
    WorkflowStatus.TORRENT_REVIEW,
    WorkflowStatus.PT_SEARCHING,
    WorkflowStatus.PT_SEARCH_PENDING,
    WorkflowStatus.NO_CANDIDATE,
    WorkflowStatus.SEARCH_FAILED,
)


def metadata_resolution_input_fingerprint(media: MediaItem) -> str:
    payload = {
        "source": media.source,
        "source_item_id": media.source_item_id,
        "media_type": media.media_type.value,
        "tmdb_id": media.tmdb_id,
        "title": media.title,
        "year": media.year,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def torrent_search_input_fingerprint(
    media: MediaItem,
    review: IdentityReview,
    request: TorrentSearchCreateRequest,
    settings: Settings,
) -> str:
    payload = {
        "media": {
            "media_type": media.media_type.value,
            "tmdb_id": media.tmdb_id,
            "title": media.title,
            "year": media.year,
            "missing_episodes": media.missing_episodes,
        },
        "identity_review_id": review.id,
        "identity_snapshot": review.candidate_snapshot,
        "request": request.model_dump(mode="json"),
        "effective_preferences": {
            "resolutions": list(request.preferred_resolutions)
            or list(settings.preferred_resolutions),
            "sources": list(request.preferred_sources) or list(settings.preferred_sources),
            "audio": list(request.preferred_audio) or list(settings.preferred_audio),
            "subtitles": list(request.preferred_subtitles)
            or list(settings.preferred_subtitles),
            "max_size_bytes": request.max_size_bytes
            or settings.max_candidate_size_bytes,
        },
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def refresh_media_search_workflow_status(
    session: AsyncSession,
    media: MediaItem,
) -> WorkflowStatus:
    locked_media = await session.get(MediaItem, media.id, with_for_update=True)
    if locked_media is None:
        raise AppError("MEDIA_NOT_FOUND", "影视条目不存在", status_code=404)
    media = locked_media
    statuses = set(
        (
            await session.scalars(
                select(TorrentSearchRun.status).where(TorrentSearchRun.media_id == media.id)
            )
        ).all()
    )
    for status in SEARCH_STATUS_PRECEDENCE:
        if status in statuses:
            media.workflow_status = status
            return status
    return media.workflow_status


async def enqueue_metadata_resolution(
    session: AsyncSession, media: MediaItem, *, max_attempts: int
) -> tuple[Job, bool]:
    if media.workflow_status == WorkflowStatus.IDENTITY_CONFIRMED:
        raise AppError("IDENTITY_ALREADY_CONFIRMED", "该影视身份已经人工确认", status_code=409)
    job_type = f"RESOLVE_METADATA:{media.id}"
    existing = await session.scalar(
        select(Job).where(Job.job_type == job_type, Job.status.in_(ACTIVE_JOB_STATUSES)).limit(1)
    )
    if existing is not None:
        return existing, True
    job = Job(
        job_type=job_type,
        status=JobStatus.PENDING,
        payload={
            "media_id": media.id,
            "input_fingerprint": metadata_resolution_input_fingerprint(media),
            "read_only": True,
        },
        max_attempts=max_attempts,
    )
    media.workflow_status = WorkflowStatus.METADATA_PENDING
    media.metadata_status = MetadataStatus.UNRESOLVED
    session.add_all(
        [
            job,
            AuditEvent(
                event_type="METADATA_RESOLUTION_QUEUED",
                entity_type="media_item",
                entity_id=media.id,
                sanitized_details={"has_tmdb_id": media.tmdb_id is not None},
            ),
        ]
    )
    await session.flush()
    return job, False


async def list_metadata_matches(session: AsyncSession, media_id: str) -> list[MetadataMatch]:
    statement = (
        select(MetadataMatch)
        .where(MetadataMatch.media_id == media_id)
        .order_by(MetadataMatch.created_at.desc(), MetadataMatch.rank.asc())
    )
    return list((await session.scalars(statement)).all())


async def confirm_identity(
    session: AsyncSession,
    media: MediaItem,
    request: IdentityConfirmationRequest,
    *,
    actor: str,
    audit_event_type: str = "IDENTITY_CONFIRMED_MANUALLY",
) -> IdentityReview:
    locked_media = await session.get(MediaItem, media.id, with_for_update=True)
    if locked_media is None:
        raise AppError("MEDIA_NOT_FOUND", "影视条目不存在", status_code=404)
    media = locked_media
    existing = await session.scalar(
        select(IdentityReview)
        .where(IdentityReview.media_id == media.id, IdentityReview.status == "CONFIRMED")
        .limit(1)
    )
    if existing is not None:
        raise AppError(
            "IDENTITY_CONFIRMATION_DUPLICATE",
            "该影视身份已经确认，禁止重复提交",
            status_code=409,
        )
    match = await session.get(MetadataMatch, request.metadata_match_id)
    if match is None or match.media_id != media.id:
        raise AppError("METADATA_MATCH_NOT_FOUND", "TMDB 候选不存在", status_code=404)
    candidate = MetadataRecord.model_validate(match.candidate_snapshot)
    conflicting = await session.scalar(
        select(MediaItem)
        .where(
            MediaItem.id != media.id,
            MediaItem.source == media.source,
            MediaItem.media_type == candidate.media_type,
            MediaItem.tmdb_id == candidate.tmdb_id,
        )
        .limit(1)
    )
    if conflicting is not None:
        raise AppError(
            "IDENTITY_UNIQUE_CONFLICT",
            "同一来源中已有影视条目使用该 TMDB ID",
            status_code=409,
        )
    review = IdentityReview(
        media_id=media.id,
        metadata_match_id=match.id,
        status="CONFIRMED",
        confirmed_by=actor,
        candidate_snapshot=candidate.model_dump(mode="json"),
    )
    media.tmdb_id = candidate.tmdb_id
    media.media_type = candidate.media_type
    media.missing_episodes = (
        derive_missing_episode_codes(
            candidate.episode_matrix,
            media.local_episode_matrix,
            media.missing_episodes,
        )
        if candidate.media_type == MediaType.TV
        else None
    )
    media.identity_confidence = IdentityConfidence.HIGH
    media.metadata_status = MetadataStatus.RESOLVED
    media.workflow_status = WorkflowStatus.IDENTITY_CONFIRMED
    session.add_all(
        [
            review,
            AuditEvent(
                event_type=audit_event_type,
                entity_type="media_item",
                entity_id=media.id,
                sanitized_details=sanitize_details(
                    {
                        "metadata_match_id": match.id,
                        "tmdb_id": candidate.tmdb_id,
                        "operator": actor,
                        "candidate_snapshot": candidate.model_dump(mode="json"),
                    }
                ),
            ),
        ]
    )
    await session.flush()
    return review


async def enqueue_torrent_search(
    session: AsyncSession,
    media: MediaItem,
    request: TorrentSearchCreateRequest,
    *,
    max_attempts: int,
    settings: Settings | None = None,
) -> tuple[TorrentSearchRun, Job, bool]:
    locked_media = await session.get(MediaItem, media.id, with_for_update=True)
    if locked_media is None:
        raise AppError("MEDIA_NOT_FOUND", "影视条目不存在", status_code=404)
    media = locked_media
    review = await session.scalar(
        select(IdentityReview)
        .where(IdentityReview.media_id == media.id, IdentityReview.status == "CONFIRMED")
        .order_by(IdentityReview.created_at.desc())
        .limit(1)
    )
    if review is None:
        raise AppError(
            "IDENTITY_CONFIRMATION_REQUIRED",
            "必须先人工确认影视身份，才能搜索 PT 候选",
            status_code=409,
        )
    existing_run = await session.scalar(
        select(TorrentSearchRun)
        .where(
            TorrentSearchRun.media_id == media.id,
            TorrentSearchRun.site_id == request.site_id,
            TorrentSearchRun.status.in_(ACTIVE_SEARCH_STATUSES),
        )
        .order_by(TorrentSearchRun.created_at.desc())
        .limit(1)
    )
    job_type = f"TORRENT_SEARCH:{request.site_id}:{media.id}"
    if existing_run is not None:
        compatible_job_types: tuple[str, ...] = (job_type,)
        if request.site_id == "avistaz":
            compatible_job_types += (f"TORRENT_SEARCH:{media.id}",)
        existing_job = await session.scalar(
            select(Job)
            .where(
                Job.job_type.in_(compatible_job_types),
                Job.status.in_(ACTIVE_JOB_STATUSES),
            )
            .limit(1)
        )
        if existing_job is not None:
            return existing_run, existing_job, True
    safe_request = request.model_dump(mode="json")
    effective_settings = settings or get_settings()
    run = TorrentSearchRun(
        media_id=media.id,
        site_id=request.site_id,
        status=WorkflowStatus.PT_SEARCH_PENDING,
        sanitized_request=safe_request,
    )
    session.add(run)
    await session.flush()
    job = Job(
        job_type=job_type,
        status=JobStatus.PENDING,
        payload={
            "media_id": media.id,
            "search_run_id": run.id,
            "site_id": request.site_id,
            "input_fingerprint": torrent_search_input_fingerprint(
                media, review, request, effective_settings
            ),
            "read_only": True,
        },
        max_attempts=max_attempts,
    )
    session.add_all(
        [
            job,
            AuditEvent(
                event_type="TORRENT_SEARCH_QUEUED",
                entity_type="torrent_search_run",
                entity_id=run.id,
                sanitized_details={"media_id": media.id, "site_id": request.site_id},
            ),
        ]
    )
    await refresh_media_search_workflow_status(session, media)
    await session.flush()
    return run, job, False


async def list_torrent_search_runs(
    session: AsyncSession, media_id: str
) -> list[TorrentSearchRun]:
    statement = (
        select(TorrentSearchRun)
        .where(TorrentSearchRun.media_id == media_id)
        .order_by(TorrentSearchRun.created_at.desc())
    )
    return list((await session.scalars(statement)).all())


async def list_torrent_candidates(
    session: AsyncSession, search_run_id: str
) -> list[TorrentCandidateRecord]:
    statement = (
        select(TorrentCandidateRecord)
        .where(TorrentCandidateRecord.search_run_id == search_run_id)
        .order_by(
            TorrentCandidateRecord.match_score.desc(),
            TorrentCandidateRecord.created_at.asc(),
        )
    )
    return list((await session.scalars(statement)).all())
