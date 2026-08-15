from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.pt_site_rules import effective_hnr_rule
from app.core.security import sanitize_details, sanitize_public_text
from app.core.time import utc_now
from app.errors import AppError
from app.models.entities import (
    ApprovalEvent,
    ApprovalRequest,
    AutomationDecision,
    AutomationPolicyRevision,
    DownloadExecution,
    DownloadExecutionEvent,
    DownloadJob,
    DownloadJobEvent,
    DownloadPlan,
    ExecutionIntent,
)
from app.models.enums import (
    ApprovalStatus,
    AutomationMode,
    AutomationStage,
    DecisionOutcome,
    DownloadExecutionStatus,
    DownloadJobStatus,
    DownloadLaunchMode,
    ExecutionIntentStatus,
    HnrStatus,
    Origin,
)
from app.schemas.executions import (
    DownloadExecutionCreateRequest,
    DownloadExecutionReconcileRequest,
    DownloadExecutionResponse,
    ExecutionIntentCreateRequest,
)
from app.schemas.qbittorrent import QbTorrent
from app.services.approvals import (
    build_qb_plan_tags,
    consume_approval,
    verify_download_plan,
    verify_snapshot,
)
from app.services.automation_policy import (
    get_current_policy,
    verify_automation_decision,
    verify_policy_revision,
)
from app.services.preflight import require_preflight_policy_current
from app.services.torrent_validation import ValidatedTorrent

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
    media_title: str,
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
    expected_tags = build_qb_plan_tags(settings.qb_plan_tags, media_title)
    if (
        plan.save_path_ref != settings.qb_save_path_ref
        or plan.category != category
        or tuple(plan.tags) != expected_tags
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
    origin: Origin = Origin.MANUAL,
    automation_policy_revision_id: str | None = None,
    automation_decision_id: str | None = None,
) -> tuple[ExecutionIntent, str]:
    require_execution_control_plane_enabled(settings)
    _require_origin_binding(
        origin,
        request.launch_mode,
        automation_policy_revision_id=automation_policy_revision_id,
        automation_decision_id=automation_decision_id,
    )
    approval = await _get_locked_approval(session, approval_id)
    now = utc_now()
    _require_approved(approval, session, now)
    snapshot = verify_snapshot(approval)
    plan = await _get_verified_plan(session, approval)
    require_preflight_policy_current(plan.preflight_policy_fingerprint, settings)
    current_target_fingerprint = qb_target_fingerprint(
        settings,
        plan,
        request.launch_mode,
        snapshot.media_title,
    )

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
        qb_target_fingerprint=current_target_fingerprint,
        launch_mode=request.launch_mode,
        origin=origin,
        automation_policy_revision_id=automation_policy_revision_id,
        automation_decision_id=automation_decision_id,
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
            "origin": intent.origin.value,
            "automation_policy_revision_id": intent.automation_policy_revision_id,
            "automation_decision_id": intent.automation_decision_id,
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
    origin: Origin = Origin.MANUAL,
    automation_policy_revision_id: str | None = None,
    automation_decision_id: str | None = None,
    execution_id: str | None = None,
) -> tuple[DownloadExecution, bool]:
    _require_origin_binding(
        origin,
        DownloadLaunchMode.ADD_PAUSED if origin == Origin.AUTOMATION else None,
        automation_policy_revision_id=automation_policy_revision_id,
        automation_decision_id=automation_decision_id,
    )
    key_digest = idempotency_key_digest(idempotency_key)
    nonce_digest = hash_secret(request.nonce)
    request_hash = _execution_request_hash(
        approval_id=approval_id,
        intent_id=request.intent_id,
        nonce_sha256=nonce_digest,
        idempotency_key_sha256=key_digest,
        origin=origin,
        automation_policy_revision_id=automation_policy_revision_id,
        automation_decision_id=automation_decision_id,
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

    require_execution_control_plane_enabled(settings)
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
    snapshot = verify_snapshot(approval)
    plan = await _get_verified_plan(session, approval)
    require_preflight_policy_current(plan.preflight_policy_fingerprint, settings)
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
    if (
        intent.origin != origin
        or intent.automation_policy_revision_id != automation_policy_revision_id
        or intent.automation_decision_id != automation_decision_id
    ):
        raise AppError(
            "EXECUTION_AUTOMATION_BINDING_MISMATCH",
            "执行意图的自动化来源绑定不一致",
            status_code=409,
        )

    current_target_fingerprint = qb_target_fingerprint(
        settings,
        plan,
        intent.launch_mode,
        snapshot.media_title,
    )
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
        **({"id": execution_id} if execution_id is not None else {}),
        approval_id=approval.id,
        intent_id=intent.id,
        idempotency_key_sha256=key_digest,
        request_hash=request_hash,
        approval_snapshot_hash=approval.snapshot_hash,
        plan_hash=plan.plan_hash,
        qb_target_fingerprint=current_target_fingerprint,
        launch_mode=intent.launch_mode,
        origin=origin,
        automation_policy_revision_id=automation_policy_revision_id,
        automation_decision_id=automation_decision_id,
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
            "origin": execution.origin.value,
            "automation_policy_revision_id": execution.automation_policy_revision_id,
            "automation_decision_id": execution.automation_decision_id,
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
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[DownloadExecution], int]:
    statement = select(DownloadExecution)
    count_statement = select(func.count()).select_from(DownloadExecution)
    if status is not None:
        statement = statement.where(DownloadExecution.status == status)
        count_statement = count_statement.where(DownloadExecution.status == status)
    statement = (
        statement.order_by(DownloadExecution.created_at.desc(), DownloadExecution.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    executions = list((await session.scalars(statement)).all())
    for execution in executions:
        await verify_download_execution(session, execution)
    total = int((await session.scalar(count_statement)) or 0)
    return executions, total


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
    lease_token: str | None = None,
    lease_seconds: int = 300,
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
    if execution.status == DownloadExecutionStatus.SUBMITTING:
        _require_execution_lease(
            execution,
            lease_token or "",
            lease_seconds=lease_seconds,
            now=await _database_now(session),
        )
    previous = execution.status
    execution.status = DownloadExecutionStatus.RECONCILIATION_REQUIRED
    execution.error_code = error_code
    execution.error_message = error_message
    execution.next_retry_at = None
    execution.locked_at = None
    execution.locked_by = None
    execution.lease_token = None
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
    lease_token: str,
    lease_seconds: int = 300,
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
    _require_execution_lease(
        execution,
        lease_token,
        lease_seconds=lease_seconds,
        now=await _database_now(session),
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
    execution.locked_at = None
    execution.locked_by = None
    execution.lease_token = None
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
    metadata: ValidatedTorrent,
    *,
    actor: str,
    lease_token: str,
    lease_seconds: int = 300,
) -> DownloadExecution:
    """Reserve submission after rechecking approval, without calling qB.

    An invalidated approval is persisted as a terminal ``CANCELLED`` execution and returned
    to the caller. This keeps the audited terminal state in the caller-owned transaction.
    """
    selected_hash = metadata.info_hash_v1 or metadata.info_hash_v2
    if selected_hash is None:
        raise AppError(
            "DOWNLOAD_EXECUTION_INFO_HASH_INVALID",
            "种子没有可持久化的实际 info hash",
            status_code=409,
        )
    execution = await get_download_execution(session, execution_id, for_update=True)
    _require_execution_lease(
        execution,
        lease_token,
        lease_seconds=lease_seconds,
        now=await _database_now(session),
    )
    if execution.status == DownloadExecutionStatus.SUBMITTING:
        if (
            execution.actual_info_hash == selected_hash
            and execution.actual_info_hash_v1 == metadata.info_hash_v1
            and execution.actual_info_hash_v2 == metadata.info_hash_v2
            and execution.actual_size_bytes == metadata.total_size_bytes
            and execution.actual_file_count == metadata.file_count
        ):
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
        return execution
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
        return execution
    plan = await _get_verified_plan(session, approval)
    expected_hash = plan.expected_info_hash.casefold() if plan.expected_info_hash else None
    if expected_hash is not None:
        if not metadata.matches_hash(expected_hash):
            raise AppError(
                "TORRENT_INFO_HASH_MISMATCH",
                "种子文件与已批准候选的 info hash 不一致",
                status_code=409,
            )
        selected_hash = expected_hash
    previous = execution.status
    previous_approval = approval.status
    approval.status = ApprovalStatus.EXECUTING
    execution.actual_info_hash = selected_hash
    execution.actual_info_hash_v1 = metadata.info_hash_v1
    execution.actual_info_hash_v2 = metadata.info_hash_v2
    execution.actual_size_bytes = metadata.total_size_bytes
    execution.actual_file_count = metadata.file_count
    execution.validated_at = now
    execution.submitted_at = now
    execution.status = DownloadExecutionStatus.SUBMITTING
    execution.next_retry_at = None
    _add_approval_event(
        session,
        approval,
        event_type="EXECUTION_RESERVED",
        actor=actor,
        details={
            "download_execution_id": execution.id,
            "actual_info_hash_v1": metadata.info_hash_v1,
            "actual_info_hash_v2": metadata.info_hash_v2,
        },
        from_status=previous_approval,
    )
    _add_execution_event(
        session,
        execution,
        event_type="SUBMISSION_STAGED",
        actor=actor,
        from_status=previous,
        to_status=DownloadExecutionStatus.SUBMITTING,
        details={
            "actual_info_hash_v1": metadata.info_hash_v1,
            "actual_info_hash_v2": metadata.info_hash_v2,
            "actual_size_bytes": metadata.total_size_bytes,
            "actual_file_count": metadata.file_count,
            "external_request_performed": False,
        },
    )
    await session.flush()
    return execution


async def finalize_download_submission(
    session: AsyncSession,
    execution_id: str,
    observed: QbTorrent,
    outcome: DownloadExecutionStatus,
    settings: Settings,
    *,
    actor: str,
    lease_token: str,
) -> DownloadJob:
    """Finalize a previously submitted torrent using an already-fetched qB observation."""
    if outcome not in {
        DownloadExecutionStatus.SUBMITTED,
        DownloadExecutionStatus.ALREADY_PRESENT,
    }:
        raise AppError(
            "DOWNLOAD_EXECUTION_OUTCOME_INVALID",
            "下载提交终态必须为 SUBMITTED 或 ALREADY_PRESENT",
            status_code=409,
        )
    execution = await get_download_execution(session, execution_id, for_update=True)
    existing_job = await session.scalar(
        select(DownloadJob)
        .where(DownloadJob.execution_id == execution.id)
        .with_for_update()
        .limit(1)
    )
    if execution.status in {
        DownloadExecutionStatus.SUBMITTED,
        DownloadExecutionStatus.ALREADY_PRESENT,
    }:
        if existing_job is None or execution.status != outcome:
            raise AppError(
                "DOWNLOAD_EXECUTION_FINALIZATION_INVALID",
                "下载执行终态与监控任务不一致",
                status_code=409,
            )
        return existing_job
    if existing_job is not None:
        raise AppError(
            "DOWNLOAD_EXECUTION_FINALIZATION_INVALID",
            "非终态下载执行已经绑定监控任务",
            status_code=409,
        )
    if execution.status != DownloadExecutionStatus.SUBMITTING:
        raise AppError(
            "DOWNLOAD_EXECUTION_TRANSITION_INVALID",
            "只有 SUBMITTING 下载执行可以确认提交结果",
            status_code=409,
        )
    _require_execution_lease(
        execution,
        lease_token,
        lease_seconds=settings.download_execution_lease_seconds,
        now=await _database_now(session),
    )

    approval = await session.scalar(
        select(ApprovalRequest)
        .where(ApprovalRequest.id == execution.approval_id)
        .with_for_update()
        .limit(1)
    )
    if approval is None or approval.status != ApprovalStatus.EXECUTING:
        raise AppError(
            "DOWNLOAD_EXECUTION_APPROVAL_INVALIDATED",
            "下载执行的审批不再处于 EXECUTING 状态",
            status_code=409,
        )
    snapshot = verify_snapshot(approval)
    await _require_final_automation_submission_cas(
        session,
        execution,
        snapshot_hit_and_run=effective_hnr_rule(
            snapshot.site_id, snapshot.hit_and_run
        ).applies,
        settings=settings,
    )
    plan = await _get_verified_plan(session, approval)
    if qb_target_fingerprint(
        settings,
        plan,
        execution.launch_mode,
        snapshot.media_title,
    ) != (
        execution.qb_target_fingerprint
    ):
        raise AppError(
            "EXECUTION_TARGET_CONFIG_CHANGED",
            "qBittorrent 执行目标与预留时不一致",
            status_code=409,
        )
    configured_save_path = settings.qb_target_save_path
    if (
        configured_save_path is None
        or not _same_qb_save_path(observed.save_path, configured_save_path)
        or observed.category != plan.category
    ):
        raise AppError(
            "QB_SUBMISSION_TARGET_MISMATCH",
            "qBittorrent 中的下载目标与已批准计划不一致",
            status_code=409,
        )
    if (
        outcome == DownloadExecutionStatus.SUBMITTED
        and execution.launch_mode == DownloadLaunchMode.ADD_PAUSED
        and observed.state.casefold()
        not in {"pauseddl", "pausedup", "stoppeddl", "stoppedup"}
    ):
        raise AppError(
            "QB_ADD_PAUSED_NOT_OBSERVED",
            "qBittorrent 未保持添加后暂停状态，禁止确认自动提交成功",
            status_code=409,
        )
    persisted_hashes = _execution_identity_hashes(execution)
    if not persisted_hashes or persisted_hashes.isdisjoint(observed.identity_hashes):
        raise AppError(
            "QB_SUBMISSION_INFO_HASH_MISMATCH",
            "qBittorrent 中的种子与已校验种子不一致",
            status_code=409,
        )
    if execution.actual_size_bytes is None or observed.size != execution.actual_size_bytes:
        raise AppError(
            "QB_SUBMISSION_SIZE_MISMATCH",
            "qBittorrent 中的种子大小与已校验种子不一致",
            status_code=409,
        )
    if execution.actual_file_count is None:
        raise AppError(
            "DOWNLOAD_EXECUTION_BINDING_INVALID",
            "下载执行缺少已校验文件数量",
            status_code=409,
        )

    now = utc_now()
    job_status = _download_job_status(observed)
    job = DownloadJob(
        execution_id=execution.id,
        approval_id=approval.id,
        media_item_id=approval.media_item_id,
        status=job_status,
        release_title=plan.release_title,
        info_hash_v1=execution.actual_info_hash_v1,
        info_hash_v2=execution.actual_info_hash_v2,
        save_path_ref=plan.save_path_ref,
        category=plan.category,
        size_bytes=execution.actual_size_bytes,
        file_count=execution.actual_file_count,
        progress=observed.progress,
        download_speed_bps=observed.dlspeed,
        upload_speed_bps=observed.upspeed,
        downloaded_bytes=observed.downloaded,
        uploaded_bytes=observed.uploaded,
        ratio=observed.ratio,
        hnr_status=HnrStatus.UNKNOWN,
        started_at=_qb_timestamp(observed.added_on) or execution.submitted_at or now,
        completed_at=_completion_time(observed, now),
        last_seen_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    await session.flush()

    await consume_approval(session, approval, actor=actor)
    previous = execution.status
    execution.status = outcome
    execution.verified_at = now
    execution.next_retry_at = None
    execution.locked_at = None
    execution.locked_by = None
    execution.lease_token = None
    execution.error_code = None
    execution.error_message = None
    _add_execution_event(
        session,
        execution,
        event_type="SUBMISSION_VERIFIED",
        actor=actor,
        from_status=previous,
        to_status=outcome,
        details={
            "download_job_id": job.id,
            "outcome": outcome.value,
            "observed_state": observed.state,
            "actual_info_hash_v1": execution.actual_info_hash_v1,
            "actual_info_hash_v2": execution.actual_info_hash_v2,
            "external_request_performed": False,
        },
    )
    _add_approval_event(
        session,
        approval,
        event_type="DOWNLOAD_JOB_CREATED",
        actor=actor,
        details={
            "download_execution_id": execution.id,
            "download_job_id": job.id,
            "outcome": outcome.value,
        },
    )
    _add_job_event(
        session,
        job,
        event_type="CREATED",
        actor=actor,
        from_status=None,
        to_status=job_status,
        details={
            "execution_id": execution.id,
            "observed_state": observed.state,
            "progress": observed.progress,
        },
    )
    await session.flush()
    return job


async def _require_final_automation_submission_cas(
    session: AsyncSession,
    execution: DownloadExecution,
    *,
    snapshot_hit_and_run: bool | None,
    settings: Settings,
) -> None:
    """Hold the policy head lock until finalization commits for automatic writes."""
    if execution.origin != Origin.AUTOMATION:
        return
    gates_enabled = all(
        (
            execution.launch_mode == DownloadLaunchMode.ADD_PAUSED,
            settings.enable_automation_engine,
            settings.enable_download_execution_control_plane,
            settings.download_executor_enabled,
            settings.enable_avistaz_live_search,
            snapshot_hit_and_run is not None,
        )
    )
    _, current = await get_current_policy(session, for_update=True)
    if (
        not gates_enabled
        or execution.automation_policy_revision_id != current.id
        or current.execution_mode != AutomationMode.AUTO_IF_ELIGIBLE
    ):
        raise AppError(
            "AUTOMATION_POLICY_CHANGED_AFTER_WRITE",
            "自动化策略在 qBittorrent 写入可能发生后变化，必须人工对账",
            status_code=409,
        )


async def update_download_job_observation(
    session: AsyncSession,
    job_id: str,
    observed: QbTorrent | None,
    *,
    expected_save_path: str,
    actor: str,
    observed_at: datetime | None = None,
) -> DownloadJob:
    """Apply an already-fetched qB snapshot to a monitored job without external I/O."""
    if not expected_save_path.strip() or any(
        marker in expected_save_path for marker in ("\x00", "\r", "\n")
    ):
        raise AppError(
            "DOWNLOAD_MONITOR_TARGET_INVALID",
            "The approved qBittorrent monitoring target is invalid",
            status_code=409,
        )
    job = await session.get(DownloadJob, job_id, with_for_update=True)
    if job is None:
        raise AppError("DOWNLOAD_JOB_NOT_FOUND", "下载任务不存在", status_code=404)
    now = observed_at or utc_now()
    previous = job.status
    if observed is None:
        job.status = DownloadJobStatus.MISSING
        job.error_code = "QB_TORRENT_MISSING"
        job.error_message = "qBittorrent 中未找到对应种子"
    else:
        if _job_identity_hashes(job).isdisjoint(observed.identity_hashes):
            raise AppError(
                "DOWNLOAD_JOB_INFO_HASH_MISMATCH",
                "qBittorrent 观察结果与下载任务不一致",
                status_code=409,
            )
        job.progress = observed.progress
        job.download_speed_bps = observed.dlspeed
        job.upload_speed_bps = observed.upspeed
        job.downloaded_bytes = observed.downloaded
        job.uploaded_bytes = observed.uploaded
        job.ratio = observed.ratio
        job.last_seen_at = now
        if (
            not _same_qb_save_path(observed.save_path, expected_save_path)
            or observed.category != job.category
            or observed.size != job.size_bytes
        ):
            job.status = DownloadJobStatus.ERROR
            job.error_code = "QB_TORRENT_BINDING_DRIFT"
            job.error_message = (
                "qBittorrent 种子的保存路径、分类或大小与已批准下载任务不一致"
            )
        else:
            job.status = _download_job_status(observed)
            if job.status == DownloadJobStatus.ERROR:
                job.error_code = "QB_TORRENT_STATE_ERROR"
                job.error_message = "qBittorrent 种子处于异常状态"
            else:
                job.error_code = None
                job.error_message = None
        completion = _completion_time(observed, now)
        if job.completed_at is None and completion is not None:
            job.completed_at = completion
    if job.status != previous:
        _add_job_event(
            session,
            job,
            event_type="STATUS_CHANGED",
            actor=actor,
            from_status=previous,
            to_status=job.status,
            details={
                "observed_state": observed.state if observed is not None else None,
                "progress": observed.progress if observed is not None else job.progress,
                "error_code": job.error_code,
            },
        )
    await session.flush()
    return job


async def record_download_job_monitor_error(
    session: AsyncSession,
    job_id: str,
    *,
    error_code: str,
    error_message: str,
    actor: str,
    details: dict[str, object] | None = None,
) -> DownloadJob:
    """Persist a safe, user-visible monitor failure without downloader mutation."""
    job = await session.get(DownloadJob, job_id, with_for_update=True)
    if job is None:
        raise AppError("DOWNLOAD_JOB_NOT_FOUND", "下载任务不存在", status_code=404)

    previous = job.status
    duplicate = previous == DownloadJobStatus.ERROR and job.error_code == error_code
    job.status = DownloadJobStatus.ERROR
    job.error_code = error_code
    job.error_message = sanitize_public_text(error_message)
    if not duplicate:
        _add_job_event(
            session,
            job,
            event_type="MONITOR_ERROR",
            actor=actor,
            from_status=previous,
            to_status=DownloadJobStatus.ERROR,
            details={**(details or {}), "error_code": error_code},
        )
    await session.flush()
    return job


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
    if execution.actual_info_hash is not None:
        bound_hashes = {
            value.casefold()
            for value in (execution.actual_info_hash_v1, execution.actual_info_hash_v2)
            if value is not None
        }
        if execution.actual_info_hash_v2 is not None:
            bound_hashes.add(execution.actual_info_hash_v2[:40].casefold())
        if execution.actual_info_hash.casefold() not in bound_hashes:
            raise AppError(
                "DOWNLOAD_EXECUTION_BINDING_INVALID",
                "下载执行记录的实际 info hash 与 v1/v2 摘要不一致",
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
    if execution.status in {
        DownloadExecutionStatus.SUBMITTING,
        DownloadExecutionStatus.SUBMITTED,
        DownloadExecutionStatus.ALREADY_PRESENT,
        DownloadExecutionStatus.OUTCOME_UNKNOWN,
        DownloadExecutionStatus.RECONCILIATION_REQUIRED,
        DownloadExecutionStatus.RECONCILIATION_PENDING,
    } and (
        execution.actual_size_bytes is None
        or execution.actual_size_bytes <= 0
        or execution.actual_file_count is None
        or execution.actual_file_count <= 0
        or execution.validated_at is None
        or execution.submitted_at is None
        or not {execution.actual_info_hash_v1, execution.actual_info_hash_v2} - {None}
    ):
        raise AppError(
            "DOWNLOAD_EXECUTION_BINDING_INVALID",
            "下载执行在提交前未持久化完整种子校验摘要",
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
    _require_origin_binding(
        execution.origin,
        execution.launch_mode,
        automation_policy_revision_id=execution.automation_policy_revision_id,
        automation_decision_id=execution.automation_decision_id,
    )
    _require_origin_binding(
        intent.origin,
        intent.launch_mode,
        automation_policy_revision_id=intent.automation_policy_revision_id,
        automation_decision_id=intent.automation_decision_id,
    )
    expected_request_hash = _execution_request_hash(
        approval_id=execution.approval_id,
        intent_id=execution.intent_id,
        nonce_sha256=intent.nonce_sha256,
        idempotency_key_sha256=execution.idempotency_key_sha256,
        origin=execution.origin,
        automation_policy_revision_id=execution.automation_policy_revision_id,
        automation_decision_id=execution.automation_decision_id,
    )
    if (
        execution.approval_snapshot_hash != approval.snapshot_hash
        or execution.approval_snapshot_hash != intent.approval_snapshot_hash
        or execution.plan_hash != plan.plan_hash
        or execution.plan_hash != intent.plan_hash
        or execution.qb_target_fingerprint != intent.qb_target_fingerprint
        or execution.launch_mode != intent.launch_mode
        or execution.origin != intent.origin
        or execution.automation_policy_revision_id
        != intent.automation_policy_revision_id
        or execution.automation_decision_id != intent.automation_decision_id
        or execution.request_hash != expected_request_hash
    ):
        raise AppError(
            "DOWNLOAD_EXECUTION_BINDING_INVALID",
            "下载执行记录与审批、意图或计划绑定不一致",
            status_code=409,
        )
    if execution.origin == Origin.AUTOMATION:
        revision = await session.get(
            AutomationPolicyRevision, execution.automation_policy_revision_id
        )
        decision = await session.get(
            AutomationDecision, execution.automation_decision_id
        )
        if revision is None or decision is None:
            raise AppError(
                "DOWNLOAD_EXECUTION_BINDING_INVALID",
                "自动下载执行缺少策略或决策绑定",
                status_code=409,
            )
        await verify_policy_revision(session, revision)
        verify_automation_decision(decision)
        if (
            decision.policy_revision_id != revision.id
            or decision.stage != AutomationStage.EXECUTION
            or decision.action != "CREATE_DOWNLOAD_EXECUTION"
            or decision.outcome != DecisionOutcome.ACTION_CREATED
            or decision.media_item_id != approval.media_item_id
            or decision.approval_request_id != approval.id
            or decision.download_execution_id != execution.id
        ):
            raise AppError(
                "DOWNLOAD_EXECUTION_BINDING_INVALID",
                "自动下载执行与策略决策记录不一致",
                status_code=409,
            )
    return execution


def requires_reconciliation(execution: DownloadExecution) -> bool:
    return execution.status in _RECONCILIATION_STATES


def download_execution_response(
    execution: DownloadExecution,
) -> DownloadExecutionResponse:
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
        actual_info_hash=execution.actual_info_hash,
        actual_info_hash_v1=execution.actual_info_hash_v1,
        actual_info_hash_v2=execution.actual_info_hash_v2,
        actual_size_bytes=execution.actual_size_bytes,
        actual_file_count=execution.actual_file_count,
        validated_at=execution.validated_at,
        submitted_at=execution.submitted_at,
        verified_at=execution.verified_at,
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


async def find_idempotent_candidate_execution(
    session: AsyncSession,
    candidate_id: str,
    idempotency_key: str,
    launch_mode: DownloadLaunchMode,
) -> DownloadExecution | None:
    key_digest = idempotency_key_digest(idempotency_key)
    execution = await session.scalar(
        select(DownloadExecution)
        .where(DownloadExecution.idempotency_key_sha256 == key_digest)
        .limit(1)
    )
    if execution is None:
        return None
    approval = await session.get(ApprovalRequest, execution.approval_id)
    if (
        approval is None
        or approval.torrent_candidate_id != candidate_id
        or execution.launch_mode != launch_mode
    ):
        raise AppError(
            "IDEMPOTENCY_KEY_REUSED",
            "Idempotency-Key 已被其他确认下载请求使用",
            status_code=409,
        )
    return await verify_download_execution(session, execution)


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
    origin: Origin = Origin.MANUAL,
    automation_policy_revision_id: str | None = None,
    automation_decision_id: str | None = None,
) -> str:
    payload: dict[str, object] = {
        "operation": "CREATE_DOWNLOAD_EXECUTION_V1",
        "approval_id": approval_id,
        "intent_id": intent_id,
        "nonce_sha256": nonce_sha256,
        "idempotency_key_sha256": idempotency_key_sha256,
    }
    if origin == Origin.AUTOMATION:
        payload.update(
            {
                "operation": "CREATE_DOWNLOAD_EXECUTION_V2",
                "origin": origin.value,
                "automation_policy_revision_id": automation_policy_revision_id,
                "automation_decision_id": automation_decision_id,
            }
        )
    return _canonical_hash(payload)


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


def _add_job_event(
    session: AsyncSession,
    job: DownloadJob,
    *,
    event_type: str,
    actor: str,
    from_status: DownloadJobStatus | None,
    to_status: DownloadJobStatus,
    details: dict[str, object],
) -> None:
    session.add(
        DownloadJobEvent(
            download_job_id=job.id,
            event_type=event_type,
            from_status=from_status.value if from_status else None,
            to_status=to_status.value,
            actor=actor.strip(),
            sanitized_details=sanitize_details(details),
        )
    )


def _execution_identity_hashes(execution: DownloadExecution) -> frozenset[str]:
    hashes = {
        value.casefold()
        for value in (
            execution.actual_info_hash,
            execution.actual_info_hash_v1,
            execution.actual_info_hash_v2,
        )
        if value
    }
    if execution.actual_info_hash_v2 is not None:
        hashes.add(execution.actual_info_hash_v2[:40].casefold())
    return frozenset(hashes)


def _job_identity_hashes(job: DownloadJob) -> frozenset[str]:
    hashes = {value.casefold() for value in (job.info_hash_v1, job.info_hash_v2) if value}
    if job.info_hash_v2 is not None:
        hashes.add(job.info_hash_v2[:40].casefold())
    return frozenset(hashes)


def _same_qb_save_path(actual: str, expected: str) -> bool:
    def normalized(value: str) -> str:
        stripped = value.strip()
        while len(stripped) > 1 and stripped.endswith(("/", "\\")):
            stripped = stripped[:-1]
        return stripped

    return normalized(actual) == normalized(expected)


def _download_job_status(observed: QbTorrent) -> DownloadJobStatus:
    state = observed.state.casefold()
    if state in {"error", "missingfiles", "unknown"}:
        return DownloadJobStatus.ERROR
    if state in {"checkingdl", "checkingup", "checkingresumedata", "moving"}:
        return DownloadJobStatus.CHECKING
    if state in {"pauseddl", "pausedup", "stoppeddl", "stoppedup"}:
        return DownloadJobStatus.PAUSED
    if state in {"queueddl", "queuedup", "allocating"}:
        return DownloadJobStatus.QUEUED
    if state in {"downloading", "forceddl", "stalleddl", "metadl"}:
        return DownloadJobStatus.DOWNLOADING
    if state in {"uploading", "forcedup", "stalledup"}:
        return DownloadJobStatus.SEEDING
    if state == "completed" or observed.progress >= 1:
        return DownloadJobStatus.COMPLETED
    return DownloadJobStatus.ERROR


def _qb_timestamp(value: int) -> datetime | None:
    if value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, tz=UTC)
    except (OSError, OverflowError, ValueError):
        return None


def _completion_time(observed: QbTorrent, now: datetime) -> datetime | None:
    completed_at = _qb_timestamp(observed.completion_on)
    if completed_at is not None:
        return completed_at
    if observed.progress >= 1:
        return now
    return None


def require_execution_control_plane_enabled(settings: Settings) -> None:
    if not settings.enable_download_execution_control_plane:
        raise AppError(
            "DOWNLOAD_EXECUTION_CONTROL_PLANE_DISABLED",
            "下载执行控制面默认关闭",
            status_code=409,
        )


def _require_origin_binding(
    origin: Origin,
    launch_mode: DownloadLaunchMode | None,
    *,
    automation_policy_revision_id: str | None,
    automation_decision_id: str | None,
) -> None:
    if origin == Origin.MANUAL:
        if automation_policy_revision_id is not None or automation_decision_id is not None:
            raise AppError(
                "EXECUTION_ORIGIN_BINDING_INVALID",
                "人工执行不能绑定自动化策略或决策",
                status_code=409,
            )
        return
    if (
        launch_mode != DownloadLaunchMode.ADD_PAUSED
        or not automation_policy_revision_id
        or not automation_decision_id
    ):
        raise AppError(
            "AUTOMATION_EXECUTION_BINDING_INVALID",
            "自动执行必须绑定策略、决策并使用添加后暂停模式",
            status_code=409,
        )


def _require_execution_lease(
    execution: DownloadExecution,
    lease_token: str,
    *,
    lease_seconds: int,
    now: datetime,
) -> None:
    if (
        lease_seconds <= 0
        or not lease_token
        or execution.lease_token is None
        or not hmac.compare_digest(execution.lease_token, lease_token)
        or execution.locked_by is None
        or execution.locked_at is None
        or _as_utc(execution.locked_at) + timedelta(seconds=lease_seconds)
        <= _as_utc(now)
    ):
        raise AppError(
            "DOWNLOAD_EXECUTION_LEASE_LOST",
            "下载执行租约已失效，禁止提交状态变更",
            status_code=409,
        )


async def _database_now(session: AsyncSession) -> datetime:
    value = await session.scalar(select(func.now()))
    return value if isinstance(value, datetime) else utc_now()


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
