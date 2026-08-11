from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import (
    AdminPrincipal,
    DbSession,
    OperatorPrincipal,
    SettingsDep,
    ViewerPrincipal,
)
from app.errors import AppError
from app.models.enums import MediaImportStatus
from app.schemas.common import Page
from app.schemas.media_imports import (
    MediaImportApproveRequest,
    MediaImportCreateRequest,
    MediaImportDecisionRequest,
    MediaImportRequestResponse,
    MediaImportRequestSummaryResponse,
)
from app.services.media_imports import (
    approve_media_import_request,
    create_media_import_request,
    list_media_import_requests,
    media_import_request_response,
    reject_media_import_request,
    revoke_media_import_request,
)
from app.services.media_imports import (
    get_media_import_request as load_media_import_request,
)

router = APIRouter(prefix="/media-import-requests", tags=["media-import-plan-only"])


@router.post("", response_model=MediaImportRequestResponse, status_code=201)
async def create_import_request(
    request: MediaImportCreateRequest,
    session: DbSession,
    settings: SettingsDep,
    principal: OperatorPrincipal,
) -> MediaImportRequestResponse:
    try:
        import_request, _plan = await create_media_import_request(
            session,
            request,
            settings,
            actor=principal.username,
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError(
            "MEDIA_IMPORT_REQUEST_CONFLICT",
            "媒体入库规划请求发生并发冲突，请重新读取下载任务状态",
            status_code=409,
        ) from exc
    return await media_import_request_response(session, import_request)


@router.get("", response_model=Page[MediaImportRequestSummaryResponse])
async def get_import_requests(
    session: DbSession,
    settings: SettingsDep,
    _principal: ViewerPrincipal,
    page: int = Query(default=1, ge=1, le=100000),
    page_size: int = Query(default=50, ge=1, le=100),
    status: MediaImportStatus | None = None,
    download_job_id: str | None = Query(default=None, min_length=1, max_length=36),
) -> Page[MediaImportRequestSummaryResponse]:
    summaries, total = await list_media_import_requests(
        session,
        settings,
        page=page,
        page_size=page_size,
        status=status,
        download_job_id=download_job_id,
    )
    return Page[MediaImportRequestSummaryResponse](
        items=summaries,
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/{request_id}", response_model=MediaImportRequestResponse)
async def get_import_request(
    request_id: str,
    session: DbSession,
    settings: SettingsDep,
    _principal: ViewerPrincipal,
) -> MediaImportRequestResponse:
    request = await load_media_import_request(
        session,
        request_id,
        settings=settings,
    )
    return await media_import_request_response(session, request)


@router.post("/{request_id}/approve", response_model=MediaImportRequestResponse)
async def approve_import_request(
    request_id: str,
    decision: MediaImportApproveRequest,
    session: DbSession,
    settings: SettingsDep,
    principal: AdminPrincipal,
) -> MediaImportRequestResponse:
    request = await approve_media_import_request(
        session,
        request_id,
        decision,
        settings,
        actor=principal.username,
    )
    await session.commit()
    return await media_import_request_response(session, request)


@router.post("/{request_id}/reject", response_model=MediaImportRequestResponse)
async def reject_import_request(
    request_id: str,
    decision: MediaImportDecisionRequest,
    session: DbSession,
    settings: SettingsDep,
    principal: AdminPrincipal,
) -> MediaImportRequestResponse:
    request = await reject_media_import_request(
        session,
        request_id,
        decision,
        settings,
        actor=principal.username,
    )
    await session.commit()
    return await media_import_request_response(session, request)


@router.post("/{request_id}/revoke", response_model=MediaImportRequestResponse)
async def revoke_import_request(
    request_id: str,
    decision: MediaImportDecisionRequest,
    session: DbSession,
    settings: SettingsDep,
    principal: AdminPrincipal,
) -> MediaImportRequestResponse:
    request = await revoke_media_import_request(
        session,
        request_id,
        decision,
        settings,
        actor=principal.username,
    )
    await session.commit()
    return await media_import_request_response(session, request)
