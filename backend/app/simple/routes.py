from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import OperatorPrincipal, ViewerPrincipal
from app.db.session import get_session
from app.simple import service
from app.simple.integrations import (
    build_nextfind,
    build_pt_site,
    build_qb,
    build_qb_readonly,
    build_tmdb,
    close_adapter,
    identify_media,
    run_release_search,
    submit_download,
    sync_download_statuses,
    sync_nextfind,
)
from app.simple.models import DownloadState, MediaState
from app.simple.schemas import (
    CandidateView,
    DownloadCreate,
    DownloadPage,
    DownloadView,
    IdentityRequest,
    MediaDetail,
    MediaPage,
    MediaSummary,
    SearchCreate,
    SearchDetail,
    SearchView,
    SyncResult,
)

router = APIRouter(tags=["daily"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/library", response_model=MediaPage)
async def library(
    session: Session,
    principal: ViewerPrincipal,
    state: MediaState | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
) -> MediaPage:
    del principal
    items, total = await service.list_media(session, state=state, page=page, page_size=page_size)
    return MediaPage(
        items=[MediaSummary.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/library/sync", response_model=SyncResult)
async def sync_library(session: Session, principal: OperatorPrincipal) -> SyncResult:
    del principal
    adapter = build_nextfind()
    try:
        created, updated = await sync_nextfind(session, adapter)
    finally:
        await close_adapter(adapter)
    return SyncResult(created=created, updated=updated)


@router.get("/library/{media_id}", response_model=MediaDetail)
async def media_detail(media_id: str, session: Session, principal: ViewerPrincipal) -> MediaDetail:
    del principal
    media, episodes = await service.get_media(session, media_id)
    return MediaDetail.model_validate(
        {**MediaSummary.model_validate(media).model_dump(), "episodes": episodes}
    )


@router.post("/library/{media_id}/identify", response_model=MediaSummary)
async def identify_library_media(
    media_id: str,
    payload: IdentityRequest,
    session: Session,
    principal: OperatorPrincipal,
) -> MediaSummary:
    del principal
    provider = build_tmdb()
    try:
        media = await identify_media(session, media_id, provider, tmdb_id=payload.tmdb_id)
    finally:
        await close_adapter(provider)
    return MediaSummary.model_validate(media)


@router.post("/library/{media_id}/searches", response_model=SearchDetail)
async def search_media(
    media_id: str,
    payload: SearchCreate,
    session: Session,
    principal: OperatorPrincipal,
) -> SearchDetail:
    del principal
    search = await service.create_search(session, media_id=media_id, site_ids=payload.site_ids)
    await run_release_search(
        session,
        search.id,
        lambda site_id: build_pt_site(site_id, allow_torrent_fetch=False),
    )
    completed, candidates = await service.get_search(session, search.id)
    return SearchDetail.model_validate(
        {
            **SearchView.model_validate(completed).model_dump(),
            "candidates": [CandidateView.model_validate(item) for item in candidates],
        }
    )


@router.get("/searches/{search_id}", response_model=SearchDetail)
async def search_detail(
    search_id: str, session: Session, principal: ViewerPrincipal
) -> SearchDetail:
    del principal
    search, candidates = await service.get_search(session, search_id)
    return SearchDetail.model_validate(
        {
            **SearchView.model_validate(search).model_dump(),
            "candidates": [CandidateView.model_validate(item) for item in candidates],
        }
    )


@router.post("/candidates/{candidate_id}/download", response_model=DownloadView)
async def download_candidate(
    candidate_id: str,
    payload: DownloadCreate,
    session: Session,
    principal: OperatorPrincipal,
) -> DownloadView:
    del principal
    download = await submit_download(
        session,
        candidate_id=candidate_id,
        confirm_warnings=payload.confirm_warnings,
        pt_factory=lambda site_id: build_pt_site(site_id, allow_torrent_fetch=True),
        qb_factory=build_qb,
    )
    return DownloadView.model_validate(download)


@router.get("/downloads", response_model=DownloadPage)
async def downloads(
    session: Session,
    principal: ViewerPrincipal,
    state: DownloadState | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
) -> DownloadPage:
    del principal
    items, total = await service.list_downloads(
        session, state=state, page=page, page_size=page_size
    )
    return DownloadPage(
        items=[DownloadView.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/downloads/sync", response_model=SyncResult)
async def sync_downloads(session: Session, principal: OperatorPrincipal) -> SyncResult:
    del principal
    qb = build_qb_readonly()
    try:
        updated = await sync_download_statuses(session, qb)
    finally:
        await close_adapter(qb)
    return SyncResult(updated=updated)
