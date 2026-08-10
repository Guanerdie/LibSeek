from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Query, Response
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import AdminPrincipal, DbSession, ViewerPrincipal
from app.core.config import get_settings
from app.errors import AppError
from app.models.entities import DownloadExecution, ExecutionIntent
from app.models.enums import DownloadExecutionStatus
from app.schemas.executions import (
    DownloadExecutionCreateRequest,
    DownloadExecutionReconcileRequest,
    DownloadExecutionResponse,
    ExecutionIntentCreateRequest,
    ExecutionIntentCreateResponse,
)
from app.services.executions import (
    create_execution_intent,
    execute_approved_plan,
    get_download_execution,
    get_execution_for_approval,
    list_download_executions,
    recover_idempotent_execution,
    request_reconciliation,
    requires_reconciliation,
)

router = APIRouter(tags=["download-executions"])


def _intent_response(
    intent: ExecutionIntent, nonce: str
) -> ExecutionIntentCreateResponse:
    return ExecutionIntentCreateResponse(
        id=intent.id,
        approval_id=intent.approval_id,
        nonce=nonce,
        status=intent.status,
        approval_snapshot_hash=intent.approval_snapshot_hash,
        plan_hash=intent.plan_hash,
        qb_target_fingerprint=intent.qb_target_fingerprint,
        launch_mode=intent.launch_mode,
        expires_at=intent.expires_at,
        created_at=intent.created_at,
    )


def _execution_response(execution: DownloadExecution) -> DownloadExecutionResponse:
    return DownloadExecutionResponse(
        id=execution.id,
        approval_id=execution.approval_id,
        intent_id=execution.intent_id,
        status=execution.status,
        requires_reconciliation=requires_reconciliation(execution),
        approval_snapshot_hash=execution.approval_snapshot_hash,
        plan_hash=execution.plan_hash,
        qb_target_fingerprint=execution.qb_target_fingerprint,
        launch_mode=execution.launch_mode,
        attempts=execution.attempts,
        max_attempts=execution.max_attempts,
        next_retry_at=execution.next_retry_at,
        locked_at=execution.locked_at,
        locked_by=execution.locked_by,
        actual_info_hash=execution.actual_info_hash,
        error_code=execution.error_code,
        error_message=execution.error_message,
        requested_by=execution.requested_by,
        requested_at=execution.requested_at,
        reconciliation_requested_by=execution.reconciliation_requested_by,
        reconciliation_requested_at=execution.reconciliation_requested_at,
        reconciliation_reason=execution.reconciliation_reason,
        created_at=execution.created_at,
        updated_at=execution.updated_at,
    )


async def _commit_safe_state_on_error(session: DbSession, error: AppError) -> None:
    # These error paths stage only their audited expiry transition before raising.
    if error.error_code in {"APPROVAL_EXPIRED", "EXECUTION_INTENT_EXPIRED"}:
        await session.commit()


@router.post(
    "/approval-requests/{approval_id}/execution-intents",
    response_model=ExecutionIntentCreateResponse,
    status_code=201,
)
async def create_approval_execution_intent(
    approval_id: str,
    request: ExecutionIntentCreateRequest,
    response: Response,
    session: DbSession,
    principal: AdminPrincipal,
) -> ExecutionIntentCreateResponse:
    try:
        intent, nonce = await create_execution_intent(
            session,
            approval_id,
            request,
            get_settings(),
            actor=principal.username,
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError(
            "EXECUTION_INTENT_CONFLICT",
            "执行意图发生并发冲突，请重新读取审批状态",
            status_code=409,
        ) from exc
    except AppError as exc:
        await _commit_safe_state_on_error(session, exc)
        raise
    response.headers["Cache-Control"] = "no-store"
    return _intent_response(intent, nonce)


@router.post(
    "/approval-requests/{approval_id}/execute",
    response_model=DownloadExecutionResponse,
    status_code=201,
)
async def execute_approval_download_plan(
    approval_id: str,
    request: DownloadExecutionCreateRequest,
    response: Response,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=16, max_length=200),
    ],
    session: DbSession,
    principal: AdminPrincipal,
) -> DownloadExecutionResponse:
    try:
        execution, created = await execute_approved_plan(
            session,
            approval_id,
            request,
            idempotency_key,
            get_settings(),
            actor=principal.username,
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        execution = await recover_idempotent_execution(
            session, approval_id, request, idempotency_key
        )
        created = False
    except AppError as exc:
        await _commit_safe_state_on_error(session, exc)
        raise
    response.status_code = 201 if created else 200
    return _execution_response(execution)


@router.get(
    "/approval-requests/{approval_id}/download-execution",
    response_model=DownloadExecutionResponse,
)
async def get_approval_download_execution(
    approval_id: str,
    session: DbSession,
    _principal: ViewerPrincipal,
) -> DownloadExecutionResponse:
    return _execution_response(await get_execution_for_approval(session, approval_id))


@router.get("/download-executions", response_model=list[DownloadExecutionResponse])
async def get_download_executions(
    session: DbSession,
    _principal: ViewerPrincipal,
    status: DownloadExecutionStatus | None = None,
    limit: int = Query(default=100, ge=1, le=200),
) -> list[DownloadExecutionResponse]:
    executions = await list_download_executions(session, status=status, limit=limit)
    return [_execution_response(execution) for execution in executions]


@router.get(
    "/download-executions/{execution_id}",
    response_model=DownloadExecutionResponse,
)
async def get_download_execution_by_id(
    execution_id: str,
    session: DbSession,
    _principal: ViewerPrincipal,
) -> DownloadExecutionResponse:
    return _execution_response(await get_download_execution(session, execution_id))


@router.post(
    "/download-executions/{execution_id}/reconcile",
    response_model=DownloadExecutionResponse,
)
async def reconcile_download_execution(
    execution_id: str,
    request: DownloadExecutionReconcileRequest,
    session: DbSession,
    principal: AdminPrincipal,
) -> DownloadExecutionResponse:
    execution = await request_reconciliation(
        session,
        execution_id,
        request,
        actor=principal.username,
    )
    await session.commit()
    return _execution_response(execution)
