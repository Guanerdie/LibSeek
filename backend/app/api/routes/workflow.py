from __future__ import annotations

from fastapi import APIRouter

from app.api.dependencies import DbSession
from app.core.config import get_settings
from app.errors import AppError
from app.models.entities import MediaItem, TorrentSearchRun
from app.schemas.adapters import MetadataRecord, TorrentCandidate
from app.schemas.entities import (
    IdentityConfirmationRequest,
    IdentityReviewResponse,
    MetadataMatchResponse,
    ResolveAccepted,
    TorrentCandidateResponse,
    TorrentSearchAccepted,
    TorrentSearchCreateRequest,
    TorrentSearchRunResponse,
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
async def resolve_media(media_id: str, session: DbSession) -> ResolveAccepted:
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
    "/media/{media_id}/metadata-candidates", response_model=list[MetadataMatchResponse]
)
async def get_metadata_candidates(
    media_id: str, session: DbSession
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
) -> IdentityReviewResponse:
    media = await _media_or_404(session, media_id)
    review = await confirm_identity(session, media, request)
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
) -> TorrentSearchAccepted:
    settings = get_settings()
    if not settings.enable_avistaz_live_search:
        raise AppError(
            "AVISTAZ_LIVE_DISABLED", "AvistaZ 真实只读搜索默认关闭", status_code=409
        )
    if not settings.avistaz_configured:
        raise AppError("AVISTAZ_NOT_CONFIGURED", "AvistaZ 运行时凭据未配置", status_code=409)
    media = await _media_or_404(session, media_id)
    run, job, deduplicated = await enqueue_torrent_search(
        session,
        media,
        request,
        max_attempts=settings.job_max_attempts,
    )
    await session.commit()
    data = _run_response(run).model_dump()
    return TorrentSearchAccepted(**data, job_id=job.id, deduplicated=deduplicated)


@router.get(
    "/media/{media_id}/torrent-searches", response_model=list[TorrentSearchRunResponse]
)
async def get_media_torrent_searches(
    media_id: str, session: DbSession
) -> list[TorrentSearchRunResponse]:
    await _media_or_404(session, media_id)
    runs = await list_torrent_search_runs(session, media_id)
    return [_run_response(run) for run in runs]


@router.get("/torrent-searches/{search_id}", response_model=TorrentSearchRunResponse)
async def get_torrent_search(search_id: str, session: DbSession) -> TorrentSearchRunResponse:
    run = await session.get(TorrentSearchRun, search_id)
    if run is None:
        raise AppError("SEARCH_RUN_NOT_FOUND", "PT 搜索任务不存在", status_code=404)
    return _run_response(run)


@router.get(
    "/torrent-searches/{search_id}/candidates",
    response_model=list[TorrentCandidateResponse],
)
async def get_torrent_candidates(
    search_id: str, session: DbSession
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

