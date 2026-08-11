from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import (
    AdminPrincipal,
    DbSession,
    OperatorPrincipal,
    QbAdapter,
    ViewerPrincipal,
)
from app.core.config import get_settings
from app.errors import AppError
from app.models.entities import ApprovalRequest
from app.models.enums import ApprovalStatus, AutomationStage
from app.schemas.approvals import (
    ApprovalApproveRequest,
    ApprovalCreateRequest,
    ApprovalDecisionRequest,
    ApprovalEventResponse,
    ApprovalRejectRequest,
    ApprovalResponse,
    ApprovalRevokeRequest,
    DownloadPlanResponse,
)
from app.services.approvals import (
    approve_request,
    create_approval_request,
    effective_status,
    get_approval_or_404,
    get_download_plan,
    list_approval_events,
    list_approval_requests,
    reject_request,
    require_pending_approval,
    revoke_request,
    save_preflight_result,
    validate_preflight_result,
)
from app.services.automation import (
    maybe_create_automatic_execution,
    maybe_enqueue_automatic_preflight,
    maybe_finalize_automatic_approval,
    require_stage_not_disabled,
)
from app.services.preflight import evaluate_preflight

router = APIRouter(tags=["approvals", "download-plans"])


async def _commit_expiration_on_error(
    session: DbSession,
    approval: ApprovalRequest,
    error: AppError,
) -> None:
    """Commit only the audited expiry staged by an approval service."""
    if (
        error.error_code == "APPROVAL_EXPIRED"
        and approval.status == ApprovalStatus.EXPIRED
    ):
        await session.commit()


async def _response(session: DbSession, approval: ApprovalRequest) -> ApprovalResponse:
    events = await list_approval_events(session, approval.id)
    return ApprovalResponse(
        id=approval.id,
        media_item_id=approval.media_item_id,
        torrent_candidate_id=approval.torrent_candidate_id,
        status=effective_status(approval),
        candidate=approval.candidate_snapshot,
        snapshot_hash=approval.snapshot_hash,
        requested_by=approval.requested_by,
        requested_at=approval.requested_at,
        expires_at=approval.expires_at,
        decided_at=approval.decided_at,
        preflight_result=(
            validate_preflight_result(approval.preflight_result)
            if approval.preflight_result is not None
            else None
        ),
        preflight_checked_at=approval.preflight_checked_at,
        events=[ApprovalEventResponse.model_validate(event) for event in events],
    )


@router.post(
    "/candidates/{candidate_id}/approval-requests",
    response_model=ApprovalResponse,
    status_code=201,
)
async def create_candidate_approval(
    candidate_id: str,
    request: ApprovalCreateRequest,
    session: DbSession,
    principal: OperatorPrincipal,
) -> ApprovalResponse:
    await require_stage_not_disabled(session, AutomationStage.TORRENT_SELECTION)
    try:
        approval = await create_approval_request(
            session,
            candidate_id,
            request,
            get_settings(),
            actor=principal.username,
        )
        await maybe_enqueue_automatic_preflight(
            session,
            approval=approval,
            settings=get_settings(),
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError(
            "APPROVAL_REQUEST_CONFLICT",
            "审批请求发生并发冲突，请重新读取候选状态",
            status_code=409,
        ) from exc
    return await _response(session, approval)


@router.get("/approval-requests", response_model=list[ApprovalResponse])
async def get_approval_requests(
    session: DbSession,
    _principal: ViewerPrincipal,
    candidate_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
) -> list[ApprovalResponse]:
    approvals = await list_approval_requests(session, candidate_id=candidate_id, limit=limit)
    return [await _response(session, approval) for approval in approvals]


@router.get("/approval-requests/{approval_id}", response_model=ApprovalResponse)
async def get_approval_request(
    approval_id: str, session: DbSession, _principal: ViewerPrincipal
) -> ApprovalResponse:
    approval = await get_approval_or_404(session, approval_id)
    return await _response(session, approval)


@router.post("/approval-requests/{approval_id}/approve", response_model=ApprovalResponse)
async def approve_approval_request(
    approval_id: str,
    request: ApprovalApproveRequest,
    session: DbSession,
    principal: AdminPrincipal,
) -> ApprovalResponse:
    await require_stage_not_disabled(session, AutomationStage.APPROVAL)
    approval = await get_approval_or_404(session, approval_id, for_update=True)
    try:
        await approve_request(
            session,
            approval,
            request,
            get_settings(),
            actor=principal.username,
        )
        await maybe_create_automatic_execution(
            session,
            approval=approval,
            settings=get_settings(),
        )
    except AppError as exc:
        await _commit_expiration_on_error(session, approval, exc)
        raise
    await session.commit()
    return await _response(session, approval)


@router.post("/approval-requests/{approval_id}/reject", response_model=ApprovalResponse)
async def reject_approval_request(
    approval_id: str,
    request: ApprovalRejectRequest,
    session: DbSession,
    principal: OperatorPrincipal,
) -> ApprovalResponse:
    approval = await get_approval_or_404(session, approval_id, for_update=True)
    try:
        await reject_request(session, approval, request, actor=principal.username)
    except AppError as exc:
        await _commit_expiration_on_error(session, approval, exc)
        raise
    await session.commit()
    return await _response(session, approval)


@router.post("/approval-requests/{approval_id}/revoke", response_model=ApprovalResponse)
async def revoke_approval_request(
    approval_id: str,
    request: ApprovalRevokeRequest,
    session: DbSession,
    principal: AdminPrincipal,
) -> ApprovalResponse:
    approval = await get_approval_or_404(session, approval_id, for_update=True)
    try:
        await revoke_request(session, approval, request, actor=principal.username)
    except AppError as exc:
        await _commit_expiration_on_error(session, approval, exc)
        raise
    await session.commit()
    return await _response(session, approval)


@router.post("/approval-requests/{approval_id}/preflight", response_model=ApprovalResponse)
async def preflight_approval_request(
    approval_id: str,
    request: ApprovalDecisionRequest,
    session: DbSession,
    principal: OperatorPrincipal,
    adapter: QbAdapter,
) -> ApprovalResponse:
    await require_stage_not_disabled(session, AutomationStage.APPROVAL)
    approval = await get_approval_or_404(session, approval_id, for_update=True)
    try:
        snapshot = require_pending_approval(session, approval)
    except AppError as exc:
        await _commit_expiration_on_error(session, approval, exc)
        raise
    snapshot_hash = approval.snapshot_hash
    await session.commit()

    # qBittorrent is read outside the approval transaction. The immutable snapshot is
    # revalidated under a fresh row lock before the observation is persisted.
    result = await evaluate_preflight(adapter, snapshot, get_settings())
    approval = await get_approval_or_404(session, approval_id, for_update=True)
    try:
        require_pending_approval(session, approval)
        if approval.snapshot_hash != snapshot_hash:
            raise AppError(
                "APPROVAL_SNAPSHOT_CHANGED",
                "审批快照已变化，预检结果已丢弃",
                status_code=409,
            )
        await save_preflight_result(session, approval, result, actor=principal.username)
        await maybe_finalize_automatic_approval(
            session,
            approval=approval,
            settings=get_settings(),
            expected_snapshot_hash=snapshot_hash,
        )
    except AppError as exc:
        await _commit_expiration_on_error(session, approval, exc)
        raise
    await session.commit()
    return await _response(session, approval)


@router.get(
    "/approval-requests/{approval_id}/download-plan",
    response_model=DownloadPlanResponse,
)
async def get_approval_download_plan(
    approval_id: str, session: DbSession, _principal: ViewerPrincipal
) -> DownloadPlanResponse:
    await get_approval_or_404(session, approval_id)
    plan = await get_download_plan(session, approval_id)
    return DownloadPlanResponse.model_validate(plan)
