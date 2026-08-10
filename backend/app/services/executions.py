from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import sanitize_details
from app.core.time import utc_now
from app.errors import AppError
from app.models.entities import (
    ApprovalEvent,
    ApprovalRequest,
    DownloadExecution,
    DownloadExecutionEvent,
    DownloadPlan,
    ExecutionIntent,
)
from app.models.enums import (
    ApprovalStatus,
    DownloadExecutionStatus,
    DownloadLaunchMode,
    ExecutionIntentStatus,
)
from app.schemas.executions import (
    DownloadExecutionCreateRequest,
    DownloadExecutionReconcileRequest,
    ExecutionIntentCreateRequest,
)
from app.services.approvals import verify_download_plan, verify_snapshot

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{15,199}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_INFO_HASH = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_RECONCILIATION_STATES = {
    DownloadExecutionStatus.OUTCOME_UNKNOWN,
    DownloadExecutionStatus.RECONCILIATION_REQUIRED,
    DownloadExecutionStatus.RECONCILIATION_PENDING,
}


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def qb_target_fingerprint(
    settings: Settings,
    plan: DownloadPlan,
    launch_mode: DownloadLaunchMode,
) -> str:
    try:
        base_url = settings.qb_base_url_value()
    except OSError as exc:
        raise AppError(
            "EXECUTION_TARGET_NOT_CONFIGURED",
            "qBittorrent 执行目标配置无法读取",
            status_code=409,
        ) from exc
    save_path = settings.qb_target_save_path
    category = settings.qb_target_category
    if not base_url or not save_path or not category or not settings.qb_allowed_hosts:
        raise AppError(
            "EXECUTION_TARGET_NOT_CONFIGURED",
            "qBittorrent 执行目标尚未完整配置",
            status_code=409,
        )
    if (
        plan.save_path_ref != settings.qb_save_path_ref
        or plan.category != category
        or tuple(plan.tags) != settings.qb_plan_tags
    ):
        raise AppError(
            "EXECUTION_TARGET_CONFIG_CHANGED",
            "qBittorrent 目标配置与已批准下载计划不一致",
            status_code=409,
        )
    target_values = (save_path, category, settings.qb_target_instance_ref, *plan.tags)
    if any(
        not value.strip() or any(marker in value for marker in ("\x00", "\r", "\n"))
        for value in target_values
    ) or (
        len(plan.tags) > 20
        or any("," in tag or len(tag) > 100 for tag in plan.tags)
    ):
        raise AppError(
            "EXECUTION_TARGET_INVALID",
            "qBittorrent 执行目标包含无效字段",
            status_code=409,
        )

    normalized_base_url = base_url.rstrip("/")
    parsed = urlparse(normalized_base_url)
    host = (parsed.hostname or "").casefold()
    allowed_schemes = {"https", "http"} if settings.qb_allow_insecure_http else {"https"}
    if (
        parsed.scheme not in allowed_schemes
        or not host
        or host not in settings.qb_allowed_hosts
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise AppError(
            "QB_URL_NOT_ALLOWED",
            "qBittorrent 地址不符合安全配置",
            status_code=400,
        )

    descriptor: dict[str, object] = {
        "downloader": "qbittorrent",
        "target_instance_ref": settings.qb_target_instance_ref,
        "base_url": normalized_base_url,
        "allowed_hosts": sorted(settings.qb_allowed_hosts),
        "allow_insecure_http": settings.qb_allow_insecure_http,
        "save_path": save_path,
        "save_path_ref": plan.save_path_ref,
        "category": plan.category,
        "tags": plan.tags,
        "launch_mode": launch_mode.value,
    }
    return _canonical_hash(descriptor)


async def create_execution_intent(
    session: AsyncSession,
    approval_id: str,
    request: ExecutionIntentCreateRequest,
    settings: Settings,
    *,
    actor: str,
) -> tuple[ExecutionIntent, str]:
    _require_control_plane_enabled(settings)
    approval = await _get_locked_approval(session, approval_id)
    now = utc_now()
    _require_approved(approval, session, now)
    plan = await _get_verified_plan(session, approval)

    existing_execution = await session.scalar(
        select(DownloadExecution).where(DownloadExecution.approval_id == approval.id).limit(1)
    )
    if existing_execution is not None:
        raise AppError(
            "DOWNLOAD_EXECUTION_ALREADY_EXISTS",
            "该审批已经创建下载执行记录",
            status_code=409,
        )

    active_intent = await session.scalar(
        select(ExecutionIntent)
        .where(
            ExecutionIntent.approval_id == approval.id,
            ExecutionIntent.status == ExecutionIntentStatus.ACTIVE,
        )
        .with_for_update()
        .limit(1)
    )
    if active_intent is not None:
        if _as_utc(active_intent.expires_at) <= _as_utc(now):
            active_intent.status = ExecutionIntentStatus.EXPIRED
            _add_approval_event(
                session,
                approval,
                event_type="EXECUTION_INTENT_EXPIRED",
                actor="system",
                details={"intent_id": active_intent.id},
            )
            await session.flush()
        else:
            raise AppError(
                "EXECUTION_INTENT_ALREADY_ACTIVE",
                "该审批已有仍然有效的执行意图",
                status_code=409,
            )

    ttl = request.expires_in_seconds or settings.execution_intent_default_ttl_seconds
    if ttl > settings.execution_intent_max_ttl_seconds:
        raise AppError(
            "EXECUTION_INTENT_TTL_TOO_LONG",
            "执行意图有效期超过系统上限",
            status_code=422,
        )
    nonce = f"ei1_{secrets.token_urlsafe(32)}"
    intent = ExecutionIntent(
        approval_id=approval.id,
        nonce_sha256=hash_secret(nonce),
        approval_snapshot_hash=approval.snapshot_hash,
        plan_hash=plan.plan_hash,
        qb_target_fingerprint=qb_target_fingerprint(settings, plan, request.launch_mode),
        launch_mode=request.launch_mode,
        status=ExecutionIntentStatus.ACTIVE,
        created_by=actor.strip(),
        expires_at=now + timedelta(seconds=ttl),
        created_at=now,
    )
    session.add(intent)
    await session.flush()
    _add_approval_event(
        session,
        approval,
        event_type="EXECUTION_INTENT_CREATED",
        actor=actor,
        details={
            "intent_id": intent.id,
            "plan_hash": intent.plan_hash,
            "qb_target_fingerprint": intent.qb_target_fingerprint,
            "launch_mode": intent.launch_mode.value,
            "expires_at": intent.expires_at.isoformat(),
        },
    )
    await session.flush()
    return intent, nonce


async def execute_approved_plan(
    session: AsyncSession,
    approval_id: str,
    request: DownloadExecutionCreateRequest,
    idempotency_key: str,
    settings: Settings,
    *,
    actor: str,
) -> tuple[DownloadExecution, bool]:
    key_digest = idempotency_key_digest(idempotency_key)
    nonce_digest = hash_secret(request.nonce)
    request_hash = _execution_request_hash(
        approval_id=approval_id,
        intent_id=request.intent_id,
        nonce_sha256=nonce_digest,
        idempotency_key_sha256=key_digest,
    )
    existing = await session.scalar(
        select(DownloadExecution)
        .where(DownloadExecution.idempotency_key_sha256 == key_digest)
        .limit(1)
    )
    if existing is not None:
        await _require_same_idempotent_request(
            session,
            existing,
            approval_id=approval_id,
            intent_id=request.intent_id,
            request_hash=request_hash,
        )
        return existing, False

    _require_control_plane_enabled(settings)
    approval = await _get_locked_approval(session, approval_id)
    now = utc_now()

    existing = await session.scalar(
        select(DownloadExecution)
        .where(DownloadExecution.idempotency_key_sha256 == key_digest)
        .limit(1)
    )
    if existing is not None:
        await _require_same_idempotent_request(
            session,
            existing,
            approval_id=approval_id,
            intent_id=request.intent_id,
            request_hash=request_hash,
        )
        return existing, False

    _require_approved(approval, session, now)
    plan = await _get_verified_plan(session, approval)
    approval_execution = await session.scalar(
        select(DownloadExecution).where(DownloadExecution.approval_id == approval.id).limit(1)
    )
    if approval_execution is not None:
        raise AppError(
            "DOWNLOAD_EXECUTION_ALREADY_EXISTS",
            "该审批已经使用其他幂等键创建下载执行记录",
            status_code=409,
        )

    intent = await session.scalar(
        select(ExecutionIntent)
        .where(ExecutionIntent.id == request.intent_id)
        .with_for_update()
        .limit(1)
    )
    if intent is None or intent.approval_id != approval.id:
        raise AppError(
            "EXECUTION_INTENT_NOT_FOUND",
            "执行意图不存在或不属于当前审批",
            status_code=404,
        )
    intent_execution = await session.scalar(
        select(DownloadExecution).where(DownloadExecution.intent_id == intent.id).limit(1)
    )
    if intent_execution is not None:
        raise AppError(
            "EXECUTION_INTENT_ALREADY_USED",
            "执行意图已经用于其他下载执行请求",
            status_code=409,
        )
    if intent.status == ExecutionIntentStatus.ACTIVE and _as_utc(intent.expires_at) <= _as_utc(
        now
    ):
        intent.status = ExecutionIntentStatus.EXPIRED
        _add_approval_event(
            session,
            approval,
            event_type="EXECUTION_INTENT_EXPIRED",
            actor="system",
            details={"intent_id": intent.id},
        )
        await session.flush()
        raise AppError("EXECUTION_INTENT_EXPIRED", "执行意图已过期", status_code=409)
    if intent.status != ExecutionIntentStatus.ACTIVE:
        raise AppError(
            "EXECUTION_INTENT_NOT_ACTIVE",
            "执行意图当前状态不可使用",
            status_code=409,
        )
    if not hmac.compare_digest(intent.nonce_sha256, nonce_digest):
        raise AppError("EXECUTION_NONCE_INVALID", "执行意图 nonce 无效", status_code=403)

    current_target_fingerprint = qb_target_fingerprint(settings, plan, intent.launch_mode)
    if (
        intent.approval_snapshot_hash != approval.snapshot_hash
        or intent.plan_hash != plan.plan_hash
        or intent.qb_target_fingerprint != current_target_fingerprint
    ):
        raise AppError(
            "EXECUTION_INTENT_BINDING_MISMATCH",
            "执行意图绑定的审批、计划或 qBittorrent 目标已经变化",
            status_code=409,
        )

    execution = DownloadExecution(
        approval_id=approval.id,
        intent_id=intent.id,
        idempotency_key_sha256=key_digest,
        request_hash=request_hash,
        approval_snapshot_hash=approval.snapshot_hash,
        plan_hash=plan.plan_hash,
        qb_target_fingerprint=current_target_fingerprint,
        launch_mode=intent.launch_mode,
        status=DownloadExecutionStatus.PENDING,
        max_attempts=settings.job_max_attempts,
        requested_by=actor.strip(),
        requested_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(execution)
    intent.status = ExecutionIntentStatus.CONSUMED
    intent.consumed_at = now
    intent.consumed_by = actor.strip()
    await session.flush()
    _add_execution_event(
        session,
        execution,
        event_type="EXECUTION_REQUESTED",
        actor=actor,
        from_status=None,
        to_status=DownloadExecutionStatus.PENDING,
        details={
            "approval_snapshot_hash": execution.approval_snapshot_hash,
            "plan_hash": execution.plan_hash,
            "qb_target_fingerprint": execution.qb_target_fingerprint,
            "launch_mode": execution.launch_mode.value,
            "control_plane_only": True,
        },
    )
    _add_approval_event(
        session,
        approval,
        event_type="DOWNLOAD_EXECUTION_REQUESTED",
        actor=actor,
        details={
            "download_execution_id": execution.id,
            "intent_id": intent.id,
            "status": execution.status.value,
            "control_plane_only": True,
        },
    )
    await session.flush()
    return execution, True


async def recover_idempotent_execution(
    session: AsyncSession,
    approval_id: str,
    request: DownloadExecutionCreateRequest,
    idempotency_key: str,
) -> DownloadExecution:
    key_digest = idempotency_key_digest(idempotency_key)
    request_hash = _execution_request_hash(
        approval_id=approval_id,
        intent_id=request.intent_id,
        nonce_sha256=hash_secret(request.nonce),
        idempotency_key_sha256=key_digest,
    )
    existing = await session.scalar(
        select(DownloadExecution)
        .where(DownloadExecution.idempotency_key_sha256 == key_digest)
        .limit(1)
    )
    if existing is not None:
        await _require_same_idempotent_request(
            session,
            existing,
            approval_id=approval_id,
            intent_id=request.intent_id,
            request_hash=request_hash,
        )
        return existing
    if await session.scalar(
        select(DownloadExecution.id).where(DownloadExecution.approval_id == approval_id).limit(1)
    ):
        raise AppError(
            "DOWNLOAD_EXECUTION_ALREADY_EXISTS",
            "该审批已经创建下载执行记录",
            status_code=409,
        )
    if await session.scalar(
        select(DownloadExecution.id)
        .where(DownloadExecution.intent_id == request.intent_id)
        .limit(1)
    ):
        raise AppError(
            "EXECUTION_INTENT_ALREADY_USED",
            "执行意图已经用于其他下载执行请求",
            status_code=409,
        )
    raise AppError(
        "DOWNLOAD_EXECUTION_CONFLICT",
        "下载执行请求发生并发冲突，请使用相同幂等键重试查询",
        status_code=409,
    )


async def get_execution_for_approval(
    session: AsyncSession, approval_id: str
) -> DownloadExecution:
    approval = await session.get(ApprovalRequest, approval_id)
    if approval is None:
        raise AppError("APPROVAL_REQUEST_NOT_FOUND", "审批请求不存在", status_code=404)
    execution = await session.scalar(
        select(DownloadExecution).where(DownloadExecution.approval_id == approval_id).limit(1)
    )
    if execution is None:
        raise AppError(
            "DOWNLOAD_EXECUTION_NOT_FOUND", "审批尚未创建下载执行记录", status_code=404
        )
    return await verify_download_execution(session, execution)


async def get_download_execution(
    session: AsyncSession, execution_id: str, *, for_update: bool = False
) -> DownloadExecution:
    execution = await session.get(DownloadExecution, execution_id, with_for_update=for_update)
    if execution is None:
        raise AppError("DOWNLOAD_EXECUTION_NOT_FOUND", "下载执行记录不存在", status_code=404)
    return await verify_download_execution(session, execution)


async def list_download_executions(
    session: AsyncSession,
    *,
    status: DownloadExecutionStatus | None = None,
    limit: int = 100,
) -> list[DownloadExecution]:
    statement = select(DownloadExecution)
    if status is not None:
        statement = statement.where(DownloadExecution.status == status)
    statement = statement.order_by(DownloadExecution.created_at.desc()).limit(limit)
    executions = list((await session.scalars(statement)).all())
    for execution in executions:
        await verify_download_execution(session, execution)
    return executions


async def request_reconciliation(
    session: AsyncSession,
    execution_id: str,
    request: DownloadExecutionReconcileRequest,
    *,
    actor: str,
) -> DownloadExecution:
    execution = await get_download_execution(session, execution_id, for_update=True)
    if execution.status == DownloadExecutionStatus.RECONCILIATION_PENDING:
        return execution
    if execution.status not in {
        DownloadExecutionStatus.OUTCOME_UNKNOWN,
        DownloadExecutionStatus.RECONCILIATION_REQUIRED,
    }:
        raise AppError(
            "DOWNLOAD_EXECUTION_NOT_RECONCILABLE",
            "只有结果不确定的下载执行记录可以请求对账",
            status_code=409,
        )
    now = utc_now()
    previous = execution.status
    execution.status = DownloadExecutionStatus.RECONCILIATION_PENDING
    execution.reconciliation_requested_by = actor.strip()
    execution.reconciliation_requested_at = now
    execution.reconciliation_reason = request.reason.strip() if request.reason else None
    _add_execution_event(
        session,
        execution,
        event_type="RECONCILIATION_REQUESTED",
        actor=actor,
        from_status=previous,
        to_status=DownloadExecutionStatus.RECONCILIATION_PENDING,
        details={
            "reason": execution.reconciliation_reason,
            "external_request_performed": False,
        },
    )
    await session.flush()
    return execution


async def mark_reconciliation_required(
    session: AsyncSession,
    execution_id: str,
    *,
    actor: str,
    error_code: str = "EXECUTION_RECONCILIATION_REQUIRED",
    error_message: str = "下载执行状态需要按 info hash 对账，禁止自动重试",
) -> DownloadExecution:
    """Internal state transition for a future executor; it performs no external request."""
    execution = await get_download_execution(session, execution_id, for_update=True)
    if execution.status == DownloadExecutionStatus.RECONCILIATION_REQUIRED:
        return execution
    if execution.status not in {
        DownloadExecutionStatus.SUBMITTING,
        DownloadExecutionStatus.OUTCOME_UNKNOWN,
    }:
        raise AppError(
            "DOWNLOAD_EXECUTION_TRANSITION_INVALID",
            "当前下载执行状态不能标记为需要对账",
            status_code=409,
        )
    previous = execution.status
    execution.status = DownloadExecutionStatus.RECONCILIATION_REQUIRED
    execution.error_code = error_code
    execution.error_message = error_message
    execution.next_retry_at = None
    _add_execution_event(
        session,
        execution,
        event_type="RECONCILIATION_REQUIRED",
        actor=actor,
        from_status=previous,
        to_status=DownloadExecutionStatus.RECONCILIATION_REQUIRED,
        details={"error_code": error_code, "automatic_retry_allowed": False},
    )
    await session.flush()
    return execution


async def mark_outcome_unknown(
    session: AsyncSession,
    execution_id: str,
    *,
    actor: str,
    error_code: str = "QB_ADD_OUTCOME_UNKNOWN",
    error_message: str = "qBittorrent 添加结果不确定，禁止自动重试",
) -> DownloadExecution:
    """Persist an uncertain external-write outcome without retrying or calling qBittorrent."""
    execution = await get_download_execution(session, execution_id, for_update=True)
    if execution.status == DownloadExecutionStatus.OUTCOME_UNKNOWN:
        return execution
    if execution.status != DownloadExecutionStatus.SUBMITTING:
        raise AppError(
            "DOWNLOAD_EXECUTION_TRANSITION_INVALID",
            "当前下载执行状态不能标记为结果不确定",
            status_code=409,
        )
    if execution.actual_info_hash is None:
        raise AppError(
            "DOWNLOAD_EXECUTION_INFO_HASH_REQUIRED",
            "进入结果不确定状态前必须持久化实际 info hash",
            status_code=409,
        )
    previous = execution.status
    execution.status = DownloadExecutionStatus.OUTCOME_UNKNOWN
    execution.error_code = error_code
    execution.error_message = error_message
    execution.next_retry_at = None
    _add_execution_event(
        session,
        execution,
        event_type="OUTCOME_UNKNOWN",
        actor=actor,
        from_status=previous,
        to_status=DownloadExecutionStatus.OUTCOME_UNKNOWN,
        details={"error_code": error_code, "automatic_retry_allowed": False},
    )
    await session.flush()
    return execution


async def persist_info_hash_before_submission(
    session: AsyncSession,
    execution_id: str,
    info_hash: str,
    *,
    actor: str,
) -> DownloadExecution:
    """Reserve submission after rechecking approval, without consuming it or calling qB."""
    normalized = info_hash.casefold()
    if not _INFO_HASH.fullmatch(normalized):
        raise AppError(
            "DOWNLOAD_EXECUTION_INFO_HASH_INVALID",
            "实际 info hash 格式无效",
            status_code=409,
        )
    execution = await get_download_execution(session, execution_id, for_update=True)
    if execution.status == DownloadExecutionStatus.SUBMITTING:
        if execution.actual_info_hash == normalized:
            return execution
        raise AppError(
            "DOWNLOAD_EXECUTION_INFO_HASH_MISMATCH",
            "已持久化的实际 info hash 与当前结果不一致",
            status_code=409,
        )
    if execution.status != DownloadExecutionStatus.VALIDATING:
        raise AppError(
            "DOWNLOAD_EXECUTION_TRANSITION_INVALID",
            "只有 VALIDATING 下载执行可以进入提交阶段",
            status_code=409,
        )
    approval = await session.scalar(
        select(ApprovalRequest)
        .where(ApprovalRequest.id == execution.approval_id)
        .with_for_update()
        .limit(1)
    )
    now = utc_now()
    if approval is None:
        await _cancel_invalidated_execution(
            session,
            execution,
            actor=actor,
            error_code="APPROVAL_REQUEST_NOT_FOUND",
        )
        raise AppError(
            "DOWNLOAD_EXECUTION_APPROVAL_INVALIDATED",
            "下载执行绑定的审批不存在，执行已取消",
            status_code=409,
        )
    verify_snapshot(approval)
    if approval.status == ApprovalStatus.APPROVED and _as_utc(
        approval.expires_at
    ) <= _as_utc(now):
        previous_approval_status = approval.status
        approval.status = ApprovalStatus.EXPIRED
        approval.decided_at = now
        _add_approval_event(
            session,
            approval,
            event_type="EXPIRED",
            actor="system",
            details={"reason": "审批有效期已结束"},
            from_status=previous_approval_status,
        )
    if approval.status != ApprovalStatus.APPROVED:
        await _cancel_invalidated_execution(
            session,
            execution,
            actor=actor,
            error_code=f"APPROVAL_{approval.status.value}",
        )
        raise AppError(
            "DOWNLOAD_EXECUTION_APPROVAL_INVALIDATED",
            "审批已撤销、过期或不再可执行，下载执行已取消",
            status_code=409,
            details={"approval_status": approval.status.value},
        )
    await _get_verified_plan(session, approval)
    previous = execution.status
    execution.actual_info_hash = normalized
    execution.status = DownloadExecutionStatus.SUBMITTING
    execution.next_retry_at = None
    _add_execution_event(
        session,
        execution,
        event_type="SUBMISSION_STAGED",
        actor=actor,
        from_status=previous,
        to_status=DownloadExecutionStatus.SUBMITTING,
        details={"actual_info_hash": normalized, "external_request_performed": False},
    )
    await session.flush()
    return execution


async def verify_download_execution(
    session: AsyncSession, execution: DownloadExecution
) -> DownloadExecution:
    digest_values = (
        execution.idempotency_key_sha256,
        execution.request_hash,
        execution.approval_snapshot_hash,
        execution.plan_hash,
        execution.qb_target_fingerprint,
    )
    if any(not _DIGEST.fullmatch(value) for value in digest_values):
        raise AppError(
            "DOWNLOAD_EXECUTION_BINDING_INVALID",
            "下载执行记录的完整性绑定无效",
            status_code=409,
        )
    if execution.actual_info_hash is not None and not _INFO_HASH.fullmatch(
        execution.actual_info_hash
    ):
        raise AppError(
            "DOWNLOAD_EXECUTION_BINDING_INVALID",
            "下载执行记录包含无效 info hash",
            status_code=409,
        )
    if execution.status in {
        DownloadExecutionStatus.SUBMITTING,
        DownloadExecutionStatus.SUBMITTED,
        DownloadExecutionStatus.ALREADY_PRESENT,
        DownloadExecutionStatus.OUTCOME_UNKNOWN,
        DownloadExecutionStatus.RECONCILIATION_REQUIRED,
        DownloadExecutionStatus.RECONCILIATION_PENDING,
    } and execution.actual_info_hash is None:
        raise AppError(
            "DOWNLOAD_EXECUTION_BINDING_INVALID",
            "下载执行在提交或对账状态前未持久化实际 info hash",
            status_code=409,
        )
    approval = await session.get(ApprovalRequest, execution.approval_id)
    intent = await session.get(ExecutionIntent, execution.intent_id)
    plan = await session.scalar(
        select(DownloadPlan).where(DownloadPlan.approval_id == execution.approval_id).limit(1)
    )
    if approval is None or intent is None or plan is None:
        raise AppError(
            "DOWNLOAD_EXECUTION_BINDING_INVALID",
            "下载执行记录缺少审批、意图或计划绑定",
            status_code=409,
        )
    verify_snapshot(approval)
    verify_download_plan(plan, approval)
    expected_request_hash = _execution_request_hash(
        approval_id=execution.approval_id,
        intent_id=execution.intent_id,
        nonce_sha256=intent.nonce_sha256,
        idempotency_key_sha256=execution.idempotency_key_sha256,
    )
    if (
        execution.approval_snapshot_hash != approval.snapshot_hash
        or execution.approval_snapshot_hash != intent.approval_snapshot_hash
        or execution.plan_hash != plan.plan_hash
        or execution.plan_hash != intent.plan_hash
        or execution.qb_target_fingerprint != intent.qb_target_fingerprint
        or execution.launch_mode != intent.launch_mode
        or execution.request_hash != expected_request_hash
    ):
        raise AppError(
            "DOWNLOAD_EXECUTION_BINDING_INVALID",
            "下载执行记录与审批、意图或计划绑定不一致",
            status_code=409,
        )
    return execution


def requires_reconciliation(execution: DownloadExecution) -> bool:
    return execution.status in _RECONCILIATION_STATES


def idempotency_key_digest(value: str) -> str:
    if not _IDEMPOTENCY_KEY.fullmatch(value):
        raise AppError(
            "IDEMPOTENCY_KEY_INVALID",
            "Idempotency-Key 必须为 16 到 200 位安全字符",
            status_code=422,
        )
    return hash_secret(value)


async def _get_locked_approval(session: AsyncSession, approval_id: str) -> ApprovalRequest:
    approval = await session.scalar(
        select(ApprovalRequest)
        .where(ApprovalRequest.id == approval_id)
        .with_for_update()
        .limit(1)
    )
    if approval is None:
        raise AppError("APPROVAL_REQUEST_NOT_FOUND", "审批请求不存在", status_code=404)
    verify_snapshot(approval)
    return approval


def _require_approved(
    approval: ApprovalRequest,
    session: AsyncSession,
    now: datetime,
) -> None:
    if approval.status == ApprovalStatus.APPROVED and _as_utc(approval.expires_at) <= _as_utc(
        now
    ):
        previous = approval.status
        approval.status = ApprovalStatus.EXPIRED
        approval.decided_at = now
        _add_approval_event(
            session,
            approval,
            event_type="EXPIRED",
            actor="system",
            details={"reason": "审批有效期已结束"},
            from_status=previous,
        )
        raise AppError("APPROVAL_EXPIRED", "审批已过期", status_code=409)
    if approval.status == ApprovalStatus.EXPIRED:
        raise AppError("APPROVAL_EXPIRED", "审批已过期", status_code=409)
    if approval.status != ApprovalStatus.APPROVED:
        raise AppError(
            "APPROVAL_NOT_EXECUTABLE",
            "只有 APPROVED 审批可以创建执行意图或下载执行记录",
            status_code=409,
        )


async def _get_verified_plan(
    session: AsyncSession, approval: ApprovalRequest
) -> DownloadPlan:
    plan = await session.scalar(
        select(DownloadPlan).where(DownloadPlan.approval_id == approval.id).limit(1)
    )
    if plan is None:
        raise AppError("DOWNLOAD_PLAN_NOT_FOUND", "审批没有对应下载计划", status_code=409)
    return verify_download_plan(plan, approval)


async def _require_same_idempotent_request(
    session: AsyncSession,
    execution: DownloadExecution,
    *,
    approval_id: str,
    intent_id: str,
    request_hash: str,
) -> None:
    if (
        execution.approval_id != approval_id
        or execution.intent_id != intent_id
        or not hmac.compare_digest(execution.request_hash, request_hash)
    ):
        raise AppError(
            "IDEMPOTENCY_KEY_REUSED",
            "Idempotency-Key 已被其他执行请求使用",
            status_code=409,
        )
    await verify_download_execution(session, execution)


def _execution_request_hash(
    *,
    approval_id: str,
    intent_id: str,
    nonce_sha256: str,
    idempotency_key_sha256: str,
) -> str:
    return _canonical_hash(
        {
            "operation": "CREATE_DOWNLOAD_EXECUTION_V1",
            "approval_id": approval_id,
            "intent_id": intent_id,
            "nonce_sha256": nonce_sha256,
            "idempotency_key_sha256": idempotency_key_sha256,
        }
    )


def _canonical_hash(value: dict[str, object]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _add_approval_event(
    session: AsyncSession,
    approval: ApprovalRequest,
    *,
    event_type: str,
    actor: str,
    details: dict[str, object],
    from_status: ApprovalStatus | None = None,
) -> None:
    session.add(
        ApprovalEvent(
            approval_request_id=approval.id,
            event_type=event_type,
            from_status=(from_status or approval.status).value,
            to_status=approval.status.value,
            actor=actor.strip(),
            snapshot_hash=approval.snapshot_hash,
            sanitized_details=sanitize_details(details),
        )
    )


async def _cancel_invalidated_execution(
    session: AsyncSession,
    execution: DownloadExecution,
    *,
    actor: str,
    error_code: str,
) -> None:
    previous = execution.status
    execution.status = DownloadExecutionStatus.CANCELLED
    execution.error_code = "APPROVAL_INVALIDATED"
    execution.error_message = "审批已撤销、过期或不存在，禁止继续提交"
    execution.next_retry_at = None
    execution.locked_at = None
    execution.locked_by = None
    execution.lease_token = None
    _add_execution_event(
        session,
        execution,
        event_type="APPROVAL_INVALIDATED",
        actor=actor,
        from_status=previous,
        to_status=DownloadExecutionStatus.CANCELLED,
        details={
            "approval_error_code": error_code,
            "automatic_retry_allowed": False,
            "external_request_performed": False,
        },
    )
    await session.flush()


def _add_execution_event(
    session: AsyncSession,
    execution: DownloadExecution,
    *,
    event_type: str,
    actor: str,
    from_status: DownloadExecutionStatus | None,
    to_status: DownloadExecutionStatus,
    details: dict[str, object],
) -> None:
    session.add(
        DownloadExecutionEvent(
            download_execution_id=execution.id,
            event_type=event_type,
            from_status=from_status.value if from_status else None,
            to_status=to_status.value,
            actor=actor.strip(),
            sanitized_details=sanitize_details(details),
        )
    )


def _require_control_plane_enabled(settings: Settings) -> None:
    if not settings.enable_download_execution_control_plane:
        raise AppError(
            "DOWNLOAD_EXECUTION_CONTROL_PLANE_DISABLED",
            "下载执行控制面默认关闭",
            status_code=409,
        )


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
