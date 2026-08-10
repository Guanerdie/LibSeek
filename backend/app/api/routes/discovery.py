from fastapi import APIRouter, Query

from app.api.dependencies import DbSession, OperatorPrincipal, ViewerPrincipal
from app.core.config import get_settings
from app.errors import AppError
from app.models.entities import DiscoveryRun
from app.repositories.discovery import list_discovery_runs, list_run_audit_events
from app.schemas.common import Page
from app.schemas.entities import (
    AuditEventResponse,
    DiscoveryRunCreated,
    DiscoveryRunDetail,
    DiscoveryRunResponse,
)
from app.services.discovery import create_discovery_run

router = APIRouter(prefix="/discovery-runs", tags=["discovery"])


@router.post("", response_model=DiscoveryRunCreated, status_code=202)
async def start_discovery(
    session: DbSession, _principal: OperatorPrincipal
) -> DiscoveryRunCreated:
    settings = get_settings()
    if not settings.nextfind_configured:
        raise AppError(
            "NEXTFIND_NOT_CONFIGURED",
            "NextFind 尚未配置运行时凭据，无法创建发现任务",
            status_code=409,
        )
    run, deduplicated = await create_discovery_run(
        session, source="nextfind", max_attempts=settings.job_max_attempts
    )
    await session.commit()
    data = DiscoveryRunResponse.model_validate(run).model_dump()
    return DiscoveryRunCreated(**data, deduplicated=deduplicated)


@router.get("", response_model=Page[DiscoveryRunResponse])
async def get_discovery_runs(
    session: DbSession,
    _principal: ViewerPrincipal,
    page: int = Query(default=1, ge=1, le=100000),
    page_size: int = Query(default=20, ge=1, le=100),
) -> Page[DiscoveryRunResponse]:
    items, total = await list_discovery_runs(session, page, page_size)
    return Page[DiscoveryRunResponse](items=items, page=page, page_size=page_size, total=total)


@router.get("/{run_id}", response_model=DiscoveryRunDetail)
async def get_discovery_run(
    run_id: str, session: DbSession, _principal: ViewerPrincipal
) -> DiscoveryRunDetail:
    run = await session.get(DiscoveryRun, run_id)
    if run is None:
        raise AppError("DISCOVERY_RUN_NOT_FOUND", "发现任务不存在", status_code=404)
    events = await list_run_audit_events(session, run_id)
    response = DiscoveryRunResponse.model_validate(run).model_dump()
    return DiscoveryRunDetail(
        **response,
        audit_events=[AuditEventResponse.model_validate(event) for event in events],
    )
