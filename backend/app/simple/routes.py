from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import OperatorPrincipal, ViewerPrincipal
from app.core.automation_runner import queue_manual_automation_run
from app.db.session import get_session
from app.errors import AppError
from app.models.enums import MediaType
from app.simple import automation, service, stats
from app.simple.integrations import (
    build_nextfind,
    build_pt_site,
    build_qb,
    build_qb_readonly,
    build_tmdb,
    candidate_details_url,
    close_adapter,
    identify_media,
    retry_download,
    run_release_search,
    submit_download,
    sync_download_statuses,
    sync_nextfind,
)
from app.simple.models import (
    AutomationJob,
    DownloadState,
    LibraryMediaItem,
    MediaState,
    ReleaseCandidate,
)
from app.simple.regions import NextFindRegion
from app.simple.schemas import (
    AutomationJobPage,
    AutomationJobView,
    AutomationOutcomeView,
    AutomationPolicyUpdate,
    AutomationPolicyView,
    AutomationRunPage,
    AutomationRunView,
    CandidateView,
    DownloadCreate,
    DownloadPage,
    DownloadView,
    IdentityRequest,
    MediaDetail,
    MediaFilterOptions,
    MediaPage,
    MediaSummary,
    SearchCreate,
    SearchDetail,
    SearchView,
    SubscriptionUpdate,
    SyncResult,
)

router = APIRouter(tags=["daily"])
Session = Annotated[AsyncSession, Depends(get_session)]


def _candidate_view(candidate: ReleaseCandidate) -> CandidateView:
    return CandidateView.model_validate(candidate).model_copy(
        update={
            "details_url": candidate_details_url(candidate.site_id, candidate.torrent_id),
        }
    )


@router.get("/library", response_model=MediaPage)
async def library(
    session: Session,
    principal: ViewerPrincipal,
    state: MediaState | None = None,
    media_type: MediaType | None = None,
    region: NextFindRegion | None = None,
    year: int | None = Query(default=None, ge=1870, le=2200),
    query: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
) -> MediaPage:
    del principal
    items, total = await service.list_media(
        session,
        state=state,
        media_type=media_type,
        region=region,
        year=year,
        query=query,
        page=page,
        page_size=page_size,
    )
    media_types, regions, states, years = await service.media_filter_options(session)
    return MediaPage(
        items=[MediaSummary.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
        filter_options=MediaFilterOptions(
            media_types=media_types,
            regions=regions,
            states=states,
            years=years,
        ),
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


def _automation_outcome(job: AutomationJob | None) -> AutomationOutcomeView | None:
    """Flatten a job's stored decision into something the UI can render."""

    if job is None:
        return None
    decision = job.decision or {}
    rejected = decision.get("rejected")
    return AutomationOutcomeView.model_validate(
        {
            "job_id": job.id,
            "state": job.state,
            "created_at": job.created_at,
            "finished_at": job.finished_at,
            "error_code": job.error_code,
            "error_message": job.error_message,
            "candidate_count": decision.get("candidate_count") or 0,
            "selected_title": decision.get("selected_title"),
            "selected_score": decision.get("selected_score"),
            "search_cooldown_until": decision.get("search_cooldown_until"),
            "download_skipped": decision.get("download_skipped"),
            "rejected": rejected if isinstance(rejected, list) else [],
        }
    )


@router.get("/library/{media_id}", response_model=MediaDetail)
async def media_detail(media_id: str, session: Session, principal: ViewerPrincipal) -> MediaDetail:
    del principal
    media, episodes, latest_search = await service.get_media(session, media_id)
    job = await service.latest_automation_job(session, media_id)
    policy = await automation.get_policy(session)
    return MediaDetail.model_validate(
        {
            **MediaSummary.model_validate(media).model_dump(),
            "episodes": episodes,
            "latest_search": (
                SearchView.model_validate(latest_search) if latest_search is not None else None
            ),
            "latest_automation": _automation_outcome(job),
            "subscribed": media.id in set(policy.selected_media_ids),
            "subscription_active": (
                media.id in set(policy.selected_media_ids) and policy.scope_mode == "selected"
            ),
        }
    )


@router.put("/library/{media_id}/subscription", response_model=MediaDetail)
async def set_subscription(
    media_id: str,
    payload: SubscriptionUpdate,
    session: Session,
    principal: OperatorPrincipal,
) -> MediaDetail:
    """Add or remove one media item from the automation scope.

    The scope already existed as ``selected_media_ids`` on the policy, but the
    only way to edit it was a multi-select box holding the whole library --
    which is why it ended up with three entries and was never revisited.
    Toggling it from the media itself is the same data, reached the way people
    actually think about it: "follow this show".
    """

    del principal
    media, episodes, latest_search = await service.get_media(session, media_id)
    policy = await automation.set_media_subscription(
        session, media_id=media.id, subscribed=payload.subscribed
    )
    job = await service.latest_automation_job(session, media_id)
    return MediaDetail.model_validate(
        {
            **MediaSummary.model_validate(media).model_dump(),
            "episodes": episodes,
            "latest_search": (
                SearchView.model_validate(latest_search) if latest_search is not None else None
            ),
            "latest_automation": _automation_outcome(job),
            "subscribed": media.id in set(policy.selected_media_ids),
            "subscription_active": (
                media.id in set(policy.selected_media_ids) and policy.scope_mode == "selected"
            ),
        }
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
    search, created = await service.get_or_create_search(
        session,
        media_id=media_id,
        site_ids=payload.site_ids,
        force=payload.force,
    )
    if created:
        await run_release_search(
            session,
            search.id,
            lambda site_id: build_pt_site(site_id, allow_torrent_fetch=False),
            metadata_factory=build_tmdb,
        )
    completed, candidates = await service.get_search(session, search.id)
    return SearchDetail.model_validate(
        {
            **SearchView.model_validate(completed).model_dump(),
            "candidates": [_candidate_view(item) for item in candidates],
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
            "candidates": [_candidate_view(item) for item in candidates],
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


@router.post("/downloads/{download_id}/retry", response_model=DownloadView)
async def retry_failed_download(
    download_id: str,
    session: Session,
    principal: OperatorPrincipal,
) -> DownloadView:
    del principal
    download = await retry_download(
        session,
        download_id=download_id,
        pt_factory=lambda site_id: build_pt_site(site_id, allow_torrent_fetch=True),
        qb_factory=build_qb,
    )
    return DownloadView.model_validate(download)


@router.post("/downloads/sync", response_model=SyncResult)
async def sync_downloads(session: Session, principal: OperatorPrincipal) -> SyncResult:
    del principal
    qb = build_qb_readonly()
    try:
        updated = await sync_download_statuses(session, qb)
    finally:
        await close_adapter(qb)
    return SyncResult(updated=updated)


@router.get("/automation/policy", response_model=AutomationPolicyView)
async def automation_policy(
    session: Session, principal: ViewerPrincipal
) -> AutomationPolicyView:
    del principal
    policy = await automation.get_policy(session)
    return AutomationPolicyView.model_validate(policy)


@router.put("/automation/policy", response_model=AutomationPolicyView)
async def save_automation_policy(
    payload: AutomationPolicyUpdate,
    session: Session,
    principal: OperatorPrincipal,
) -> AutomationPolicyView:
    del principal
    policy = await automation.update_policy(session, payload)
    return AutomationPolicyView.model_validate(policy)


@router.get("/automation/jobs", response_model=AutomationJobPage)
async def automation_jobs(
    session: Session,
    principal: ViewerPrincipal,
    run_id: str | None = Query(default=None, min_length=1, max_length=36),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
) -> AutomationJobPage:
    del principal
    rows, total = await automation.list_jobs(
        session, page=page, page_size=page_size, run_id=run_id
    )
    return AutomationJobPage(
        items=[
            AutomationJobView.model_validate({**job.__dict__, "media_title": title})
            for job, title in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/automation/runs",
    response_model=AutomationRunView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_automation(
    session: Session, principal: OperatorPrincipal
) -> AutomationRunView:
    del principal
    run = await automation.create_automation_run(session, trigger="manual")
    queue_manual_automation_run(run.id)
    return AutomationRunView.model_validate(run)


@router.get("/automation/runs", response_model=AutomationRunPage)
async def automation_runs(
    session: Session,
    principal: ViewerPrincipal,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
) -> AutomationRunPage:
    """List executions for the automation history screen.

    This endpoint deliberately returns run summaries only.  The potentially
    long per-media task list is fetched from the scoped ``/jobs`` endpoint when
    a user opens a run.
    """

    del principal
    runs, total = await automation.list_automation_runs(
        session, page=page, page_size=page_size
    )
    return AutomationRunPage(
        items=[AutomationRunView.model_validate(run) for run in runs],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/automation/runs/latest", response_model=AutomationRunView | None)
async def latest_automation_run(
    session: Session, principal: ViewerPrincipal
) -> AutomationRunView | None:
    del principal
    run = await automation.get_latest_automation_run(session)
    return AutomationRunView.model_validate(run) if run is not None else None


@router.get("/automation/runs/{run_id}", response_model=AutomationRunView)
async def automation_run_status(
    run_id: str, session: Session, principal: ViewerPrincipal
) -> AutomationRunView:
    del principal
    run = await automation.get_automation_run(session, run_id)
    return AutomationRunView.model_validate(run)


@router.get("/automation/runs/{run_id}/jobs", response_model=AutomationJobPage)
async def automation_run_jobs(
    run_id: str,
    session: Session,
    principal: ViewerPrincipal,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
) -> AutomationJobPage:
    """List tasks for a single execution, with independent pagination."""

    del principal
    # Keep the same not-found contract as the existing run status endpoint;
    # otherwise a typo would silently look like an empty successful run.
    await automation.get_automation_run(session, run_id)
    rows, total = await automation.list_run_jobs(
        session, run_id, page=page, page_size=page_size
    )
    return AutomationJobPage(
        items=[
            AutomationJobView.model_validate({**job.__dict__, "media_title": title})
            for job, title in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/automation/jobs/{job_id}/retry", response_model=AutomationJobView)
async def retry_automation_job(
    job_id: str, session: Session, principal: OperatorPrincipal
) -> AutomationJobView:
    del principal
    job, run = await automation.retry_job(session, job_id)
    queue_manual_automation_run(run.id)
    media = await session.get(LibraryMediaItem, job.media_id)
    if media is None:
        raise AppError("MEDIA_NOT_FOUND", "影视条目不存在", status_code=404)
    return AutomationJobView.model_validate({**job.__dict__, "media_title": media.title})


@router.get("/stats")
async def automation_stats(
    session: Session, principal: ViewerPrincipal
) -> dict[str, object]:
    """Aggregates for the monitoring page.

    Read-only and viewer-visible: it exposes counts and durations the operator
    can already see one page at a time, nothing new.
    """

    del principal
    return await stats.collect_stats(session)


@router.get("/automation/score-distribution")
async def automation_score_distribution(
    session: Session, principal: ViewerPrincipal
) -> dict[str, object]:
    """Candidate score spread, for the minimum-score setting to show against."""

    del principal
    return await stats.score_distribution(session)
