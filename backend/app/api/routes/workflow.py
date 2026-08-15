from __future__ import annotations

from fastapi import APIRouter, Response

from app.api.dependencies import DbSession, OperatorPrincipal, PtCatalog, ViewerPrincipal
from app.core.config import get_settings
from app.errors import AppError
from app.models.entities import Job, MediaItem, TorrentSearchRun
from app.models.enums import AutomationStage
from app.schemas.adapters import MetadataRecord, TorrentCandidate
from app.schemas.entities import (
    IdentityConfirmationRequest,
    IdentityReviewResponse,
    MetadataMatchResponse,
    MetadataResolutionJobResponse,
    ResolveAccepted,
    TorrentCandidateResponse,
    TorrentSearchAccepted,
    TorrentSearchCreateRequest,
    TorrentSearchRunResponse,
)
from app.services.automation import (
    maybe_automate_torrent_search_after_identity,
    require_stage_not_disabled,
)
from app.services.workflow import (
    confirm_identity,
    enqueue_metadata_resolution,
    enqueue_torrent_search,
    list_metadata_matches,
    list_torrent_candidates,
    list_torrent_search_runs,
)

router = APIRouter(tags=["identity", "torrent-search"])


async def _media_or_404(session: DbSession, media_id: str) -> MediaItem:
    media = await session.get(MediaItem, media_id)
    if media is None:
        raise AppError("MEDIA_NOT_FOUND", "影视条目不存在", status_code=404)
    return media


def _run_response(run: TorrentSearchRun) -> TorrentSearchRunResponse:
    return TorrentSearchRunResponse.model_validate(run)


@router.post("/media/{media_id}/resolve", response_model=ResolveAccepted, status_code=202)
async def resolve_media(
    media_id: str, session: DbSession, _principal: OperatorPrincipal
) -> ResolveAccepted:
    await require_stage_not_disabled(session, AutomationStage.IDENTITY)
    settings = get_settings()
    if not settings.enable_tmdb_live:
        raise AppError("TMDB_LIVE_DISABLED", "TMDB 真实只读连接默认关闭", status_code=409)
    if not settings.tmdb_configured:
        raise AppError("TMDB_NOT_CONFIGURED", "TMDB Access Token 未配置", status_code=409)
    media = await _media_or_404(session, media_id)
    job, deduplicated = await enqueue_metadata_resolution(
        session, media, max_attempts=settings.job_max_attempts
    )
    await session.commit()
    return ResolveAccepted(
        media_id=media.id,
        job_id=job.id,
        status=media.workflow_status,
        deduplicated=deduplicated,
    )


@router.get(
    "/media/{media_id}/resolve-jobs/{job_id}",
    response_model=MetadataResolutionJobResponse,
)
async def get_metadata_resolution_job(
    media_id: str,
    job_id: str,
    response: Response,
    session: DbSession,
    _principal: ViewerPrincipal,
) -> MetadataResolutionJobResponse:
    await _media_or_404(session, media_id)
    job = await session.get(Job, job_id)
    if job is None or job.job_type != f"RESOLVE_METADATA:{media_id}":
        raise AppError(
            "METADATA_RESOLUTION_JOB_NOT_FOUND",
            "TMDB 解析任务不存在",
            status_code=404,
        )
    payload_media_id = job.payload.get("media_id") if isinstance(job.payload, dict) else None
    if payload_media_id != media_id:
        raise AppError(
            "METADATA_RESOLUTION_JOB_BINDING_INVALID",
            "TMDB 解析任务与当前影视绑定无效",
            status_code=409,
        )
    response.headers["Cache-Control"] = "no-store"
    return MetadataResolutionJobResponse(
        media_id=media_id,
        job_id=job.id,
        status=job.status,
        error_code=job.error_code,
        error_message=job.error_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.get(
    "/media/{media_id}/metadata-candidates", response_model=list[MetadataMatchResponse]
)
async def get_metadata_candidates(
    media_id: str, session: DbSession, _principal: ViewerPrincipal
) -> list[MetadataMatchResponse]:
    await _media_or_404(session, media_id)
    matches = await list_metadata_matches(session, media_id)
    return [
        MetadataMatchResponse(
            id=match.id,
            media_id=match.media_id,
            tmdb_id=match.tmdb_id,
            rank=match.rank,
            score=match.score,
            match_reasons=match.match_reasons,
            conflicts=match.conflicts,
            candidate=MetadataRecord.model_validate(match.candidate_snapshot),
            created_at=match.created_at,
        )
        for match in matches
    ]


@router.post(
    "/media/{media_id}/identity-confirmations",
    response_model=IdentityReviewResponse,
    status_code=201,
)
async def create_identity_confirmation(
    media_id: str,
    request: IdentityConfirmationRequest,
    session: DbSession,
    principal: OperatorPrincipal,
) -> IdentityReviewResponse:
    await require_stage_not_disabled(session, AutomationStage.IDENTITY)
    media = await _media_or_404(session, media_id)
    review = await confirm_identity(session, media, request, actor=principal.username)
    await maybe_automate_torrent_search_after_identity(
        session,
        media=media,
        trigger_created_at=review.created_at,
        settings=get_settings(),
    )
    await session.commit()
    return IdentityReviewResponse(
        id=review.id,
        media_id=review.media_id,
        metadata_match_id=review.metadata_match_id,
        status=review.status,
        confirmed_by=review.confirmed_by,
        candidate=MetadataRecord.model_validate(review.candidate_snapshot),
        created_at=review.created_at,
    )


@router.post(
    "/media/{media_id}/torrent-searches",
    response_model=TorrentSearchAccepted,
    status_code=202,
)
async def create_torrent_search(
    media_id: str,
    request: TorrentSearchCreateRequest,
    session: DbSession,
    catalog: PtCatalog,
    _principal: OperatorPrincipal,
) -> TorrentSearchAccepted:
    catalog.require_searchable(request.site_id)
    await require_stage_not_disabled(session, AutomationStage.TORRENT_SELECTION)
    settings = get_settings()
    media = await _media_or_404(session, media_id)
    catalog.require_searchable(request.site_id, media_type=media.media_type)
    run, job, deduplicated = await enqueue_torrent_search(
        session,
        media,
        request,
        max_attempts=settings.job_max_attempts,
        settings=settings,
    )
    await session.commit()
    data = _run_response(run).model_dump()
    return TorrentSearchAccepted(**data, job_id=job.id, deduplicated=deduplicated)


@router.get(
    "/media/{media_id}/torrent-searches", response_model=list[TorrentSearchRunResponse]
)
async def get_media_torrent_searches(
    media_id: str, session: DbSession, _principal: ViewerPrincipal
) -> list[TorrentSearchRunResponse]:
    await _media_or_404(session, media_id)
    runs = await list_torrent_search_runs(session, media_id)
    return [_run_response(run) for run in runs]


@router.get("/torrent-searches/{search_id}", response_model=TorrentSearchRunResponse)
async def get_torrent_search(
    search_id: str, session: DbSession, _principal: ViewerPrincipal
) -> TorrentSearchRunResponse:
    run = await session.get(TorrentSearchRun, search_id)
    if run is None:
        raise AppError("SEARCH_RUN_NOT_FOUND", "PT 搜索任务不存在", status_code=404)
    return _run_response(run)


@router.get(
    "/torrent-searches/{search_id}/candidates",
    response_model=list[TorrentCandidateResponse],
)
async def get_torrent_candidates(
    search_id: str, session: DbSession, _principal: ViewerPrincipal
) -> list[TorrentCandidateResponse]:
    run = await session.get(TorrentSearchRun, search_id)
    if run is None:
        raise AppError("SEARCH_RUN_NOT_FOUND", "PT 搜索任务不存在", status_code=404)
    candidates = await list_torrent_candidates(session, search_id)
    return [
        TorrentCandidateResponse(
            id=candidate.id,
            search_run_id=candidate.search_run_id,
            candidate=TorrentCandidate.model_validate(candidate.candidate_snapshot),
            match_score=candidate.match_score,
            match_reasons=candidate.match_reasons,
            warnings=candidate.warnings,
            created_at=candidate.created_at,
        )
        for candidate in candidates
    ]
