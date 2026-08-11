from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.dependencies import DbSession, ViewerPrincipal
from app.models.entities import DownloadJob
from app.models.enums import DownloadJobStatus
from app.schemas.common import Page
from app.schemas.jobs import (
    DownloadJobResponse,
    DownloadJobSummaryResponse,
    DownloadJobTimelineResponse,
)
from app.services.jobs import (
    get_download_job,
    get_download_job_summary,
    list_download_job_timeline,
    list_download_jobs,
)

router = APIRouter(prefix="/download-jobs", tags=["download-jobs"])


def _job_response(job: DownloadJob) -> DownloadJobResponse:
    return DownloadJobResponse.model_validate(job)


@router.get("", response_model=Page[DownloadJobResponse])
async def get_download_jobs(
    session: DbSession,
    _principal: ViewerPrincipal,
    page: int = Query(default=1, ge=1, le=100000),
    page_size: int = Query(default=50, ge=1, le=100),
    status: DownloadJobStatus | None = None,
) -> Page[DownloadJobResponse]:
    jobs, total = await list_download_jobs(
        session,
        page=page,
        page_size=page_size,
        status=status,
    )
    return Page[DownloadJobResponse](
        items=[_job_response(job) for job in jobs],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/{job_id}", response_model=DownloadJobResponse)
async def get_download_job_by_id(
    job_id: str,
    session: DbSession,
    _principal: ViewerPrincipal,
) -> DownloadJobResponse:
    return _job_response(await get_download_job(session, job_id))


@router.get(
    "/{job_id}/timeline",
    response_model=DownloadJobTimelineResponse,
)
async def get_download_job_timeline(
    job_id: str,
    session: DbSession,
    _principal: ViewerPrincipal,
) -> DownloadJobTimelineResponse:
    return await list_download_job_timeline(session, job_id)


@router.get("/{job_id}/summary", response_model=DownloadJobSummaryResponse)
async def get_download_job_summary_by_id(
    job_id: str,
    session: DbSession,
    _principal: ViewerPrincipal,
) -> DownloadJobSummaryResponse:
    return await get_download_job_summary(session, job_id)
