from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.config import Settings
from app.core.security import sanitize_details
from app.core.time import utc_now
from app.errors import AppError
from app.models.entities import (
    ApprovalRequest,
    DownloadExecution,
    DownloadJob,
    MediaImportEvent,
    MediaImportPlan,
    MediaImportPreflight,
    MediaImportRequest,
    MediaItem,
)
from app.models.enums import (
    ApprovalStatus,
    DownloadExecutionStatus,
    DownloadJobStatus,
    HnrStatus,
    MediaImportOperation,
    MediaImportStatus,
    PreflightStatus,
)
from app.schemas.media_imports import (
    MEDIA_IMPORT_PLAN_MODE,
    MediaImportApproveRequest,
    MediaImportCreateRequest,
    MediaImportDecisionAcknowledgements,
    MediaImportDecisionRequest,
    MediaImportEventResponse,
    MediaImportInspectionSnapshot,
    MediaImportJobSummarySnapshot,
    MediaImportPlanResponse,
    MediaImportPreflightCheck,
    MediaImportPreflightResponse,
    MediaImportPreflightResult,
    MediaImportRequestResponse,
    MediaImportRequestSummaryResponse,
    MediaImportSourceManifest,
    MediaImportTargetMapping,
    normalize_media_import_reason,
    validate_internal_root_ref,
)
from app.services.executions import requires_reconciliation, verify_download_execution

_ELIGIBLE_JOB_STATUSES = {
    DownloadJobStatus.SEEDING,
    DownloadJobStatus.COMPLETED,
    DownloadJobStatus.PAUSED,
}
_FINAL_EXECUTION_STATUSES = {
    DownloadExecutionStatus.SUBMITTED,
    DownloadExecutionStatus.ALREADY_PRESENT,
}
_ACTIVE_REQUEST_STATUSES = {
    MediaImportStatus.PREFLIGHT_REQUIRED,
    MediaImportStatus.REVIEW_REQUIRED,
    MediaImportStatus.APPROVED_PLAN_ONLY,
}
MEDIA_IMPORT_PREFLIGHT_CHECK_CODES = (
    "SUMMARY_BINDING",
    "DOWNLOAD_READY",
    "SOURCE_MANIFEST_BINDING",
    "SOURCE_FILES_SAFE",
    "SOURCE_FILES_COMPLETE",
    "TARGET_MAPPING_BINDING",
    "TARGET_COLLISION_FREE",
    "OPERATION_CAPABILITY",
    "SOURCE_RETENTION_POLICY",
    "NO_OVERWRITE_POLICY",
    "HNR_DISCLOSURE",
)
_STATUS_SEVERITY = {
    PreflightStatus.PASS: 0,
    PreflightStatus.WARNING: 1,
    PreflightStatus.UNKNOWN: 2,
    PreflightStatus.BLOCKED: 3,
}


def require_media_import_enabled(settings: Settings) -> None:
    if not settings.enable_media_import_control_plane:
        raise AppError(
            "MEDIA_IMPORT_DISABLED",
            "可选媒体入库规划控制面默认关闭",
            status_code=409,
        )


def canonical_hash(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def media_import_config_fingerprint(settings: Settings) -> str:
    roots = _validated_target_roots(settings)
    payload = {
        "schema_version": 1,
        "plan_mode": MEDIA_IMPORT_PLAN_MODE,
        "allowed_operations": sorted(operation.value for operation in MediaImportOperation),
        "target_root_refs": sorted(roots, key=str.casefold),
        "source_retention": True,
        "overwrite_allowed": False,
        "preflight_max_age_seconds": settings.media_import_preflight_max_age_seconds,
    }
    return canonical_hash(payload)


def media_import_plan_hash(plan: MediaImportPlan) -> str:
    return canonical_hash(
        {
            "request_id": plan.request_id,
            "download_job_id": plan.download_job_id,
            "media_item_id": plan.media_item_id,
            "execution_id": plan.execution_id,
            "mode": plan.mode,
            "proposed_operation": plan.proposed_operation.value,
            "source_manifest_hash": plan.source_manifest_hash,
            "target_mapping_hash": plan.target_mapping_hash,
            "summary_snapshot_hash": plan.summary_snapshot_hash,
            "config_fingerprint": plan.config_fingerprint,
            "source_retention": plan.source_retention,
            "overwrite_allowed": plan.overwrite_allowed,
        }
    )


def media_import_preflight_hash(preflight: MediaImportPreflight) -> str:
    return canonical_hash(
        {
            "request_id": preflight.request_id,
            "plan_id": preflight.plan_id,
            "overall_status": preflight.overall_status.value,
            "inspection_hash": preflight.inspection_hash,
            "result_hash": preflight.result_hash,
            "config_fingerprint": preflight.config_fingerprint,
            "checked_by": preflight.checked_by,
            "checked_at": _as_utc(preflight.checked_at).isoformat(),
        }
    )


async def create_media_import_request(
    session: AsyncSession,
    request_data: MediaImportCreateRequest,
    settings: Settings,
    *,
    actor: str,
) -> tuple[MediaImportRequest, MediaImportPlan]:
    require_media_import_enabled(settings)
    actor = _validate_actor(actor)
    target_roots = _validated_target_roots(settings)
    if request_data.target_mapping.target_root_ref not in target_roots:
        raise AppError(
            "MEDIA_IMPORT_TARGET_NOT_ALLOWED",
            "媒体入库目标根引用不在服务端白名单中",
            status_code=409,
        )

    job, media, execution, approval = await _load_bound_entities(
        session,
        request_data.download_job_id,
        for_update=True,
    )
    _require_download_ready(job, execution, approval)
    if request_data.source_manifest.source_root_ref != job.save_path_ref:
        raise AppError(
            "MEDIA_IMPORT_SOURCE_REF_MISMATCH",
            "未受信源清单未绑定下载任务保存位置引用",
            status_code=409,
        )
    if request_data.target_mapping.target_root_ref == job.save_path_ref:
        raise AppError(
            "MEDIA_IMPORT_TARGET_INVALID",
            "媒体库目标引用必须与下载源引用分离",
            status_code=409,
        )
    _validate_proposal_bindings(job, request_data)

    existing = await session.scalar(
        select(MediaImportRequest)
        .where(
            MediaImportRequest.download_job_id == job.id,
            MediaImportRequest.status.in_(_ACTIVE_REQUEST_STATUSES),
        )
        .limit(1)
    )
    if existing is not None:
        raise AppError(
            "MEDIA_IMPORT_REQUEST_CONFLICT",
            "下载任务已有有效的媒体入库规划请求",
            status_code=409,
        )

    summary = _job_summary_snapshot(job, media, execution)
    source_manifest = request_data.source_manifest.model_dump(mode="json")
    target_mapping = request_data.target_mapping.model_dump(mode="json")
    summary_snapshot = summary.model_dump(mode="json")
    now = utc_now()
    import_request = MediaImportRequest(
        download_job_id=job.id,
        media_item_id=media.id,
        execution_id=execution.id,
        status=MediaImportStatus.PREFLIGHT_REQUIRED,
        requested_by=actor,
        requested_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(import_request)
    await session.flush()

    plan = MediaImportPlan(
        request_id=import_request.id,
        download_job_id=job.id,
        media_item_id=media.id,
        execution_id=execution.id,
        mode=MEDIA_IMPORT_PLAN_MODE,
        proposed_operation=request_data.proposed_operation,
        source_manifest=source_manifest,
        source_manifest_hash=canonical_hash(source_manifest),
        target_mapping=target_mapping,
        target_mapping_hash=canonical_hash(target_mapping),
        job_summary_snapshot=summary_snapshot,
        summary_snapshot_hash=canonical_hash(summary_snapshot),
        config_fingerprint=media_import_config_fingerprint(settings),
        plan_hash="",
        source_retention=True,
        overwrite_allowed=False,
        created_by=actor,
        created_at=now,
    )
    plan.plan_hash = media_import_plan_hash(plan)
    session.add(plan)
    _add_event(
        session,
        import_request,
        event_type="MEDIA_IMPORT_PLAN_PROPOSAL_CREATED",
        from_status=None,
        actor=actor,
        details={
            "plan_hash": plan.plan_hash,
            "source_manifest_hash": plan.source_manifest_hash,
            "target_mapping_hash": plan.target_mapping_hash,
            "proposed_operation": plan.proposed_operation.value,
            "trusted_inspection_required": True,
            "mode": MEDIA_IMPORT_PLAN_MODE,
        },
    )
    await session.flush()
    return import_request, plan


async def record_media_import_preflight(
    session: AsyncSession,
    request_id: str,
    inspection: MediaImportInspectionSnapshot,
    settings: Settings,
    *,
    actor: str,
) -> MediaImportPreflight:
    """Persist a trusted read-only observation; this function has no public API route."""

    require_media_import_enabled(settings)
    actor = _validate_actor(actor)
    import_request = await get_media_import_request(
        session, request_id, for_update=True, require_enabled=False
    )
    if import_request.status not in {
        MediaImportStatus.PREFLIGHT_REQUIRED,
        MediaImportStatus.REVIEW_REQUIRED,
    }:
        raise AppError(
            "MEDIA_IMPORT_PREFLIGHT_STATE_INVALID",
            "媒体入库请求当前状态不允许记录只读预检",
            status_code=409,
        )
    plan = await _get_plan(session, import_request.id)
    verify_media_import_plan(plan, import_request)
    current_fingerprint = media_import_config_fingerprint(settings)
    if plan.config_fingerprint != current_fingerprint:
        raise AppError(
            "MEDIA_IMPORT_CONFIG_CHANGED",
            "媒体入库规划配置已变化，请创建新计划",
            status_code=409,
        )
    now = utc_now()
    inspected_at = _as_utc(inspection.inspected_at)
    if inspected_at > now or (now - inspected_at).total_seconds() > (
        settings.media_import_preflight_max_age_seconds
    ):
        raise AppError(
            "MEDIA_IMPORT_INSPECTION_STALE",
            "受信只读检查快照时间无效或已过期",
            status_code=409,
        )

    job, media, execution, approval = await _load_bound_entities(
        session, import_request.download_job_id, for_update=True
    )
    current_summary = _job_summary_snapshot(job, media, execution)
    _verify_inspection_proposal_binding(plan, inspection)
    result = _evaluate_inspection(
        plan,
        inspection,
        current_summary=current_summary,
        approval=approval,
        checked_at=now,
    )
    inspection_payload = inspection.model_dump(mode="json")
    result_payload = result.model_dump(mode="json")
    preflight = MediaImportPreflight(
        request_id=import_request.id,
        plan_id=plan.id,
        overall_status=result.overall_status,
        inspection_snapshot=inspection_payload,
        inspection_hash=canonical_hash(inspection_payload),
        result=result_payload,
        result_hash=canonical_hash(result_payload),
        preflight_hash="",
        config_fingerprint=current_fingerprint,
        checked_by=actor,
        checked_at=now,
        created_at=now,
    )
    preflight.preflight_hash = media_import_preflight_hash(preflight)
    session.add(preflight)
    previous = import_request.status
    import_request.status = (
        MediaImportStatus.REVIEW_REQUIRED
        if preflight.overall_status in {PreflightStatus.PASS, PreflightStatus.WARNING}
        else MediaImportStatus.PREFLIGHT_REQUIRED
    )
    import_request.updated_at = now
    _add_event(
        session,
        import_request,
        event_type="MEDIA_IMPORT_TRUSTED_PREFLIGHT_RECORDED",
        from_status=previous,
        actor=actor,
        details={
            "plan_hash": plan.plan_hash,
            "inspection_hash": preflight.inspection_hash,
            "preflight_hash": preflight.preflight_hash,
            "overall_status": preflight.overall_status.value,
            "read_only": True,
        },
    )
    await session.flush()
    return preflight


async def approve_media_import_request(
    session: AsyncSession,
    request_id: str,
    decision: MediaImportApproveRequest,
    settings: Settings,
    *,
    actor: str,
) -> MediaImportRequest:
    require_media_import_enabled(settings)
    actor = _validate_actor(actor)
    import_request = await get_media_import_request(
        session, request_id, for_update=True, require_enabled=False
    )
    if import_request.status != MediaImportStatus.REVIEW_REQUIRED:
        raise AppError(
            "MEDIA_IMPORT_APPROVAL_STATE_INVALID",
            "媒体入库请求尚未完成受信预检或已被处理",
            status_code=409,
        )
    plan = await _get_plan(session, import_request.id)
    preflight = await _get_preflight(session, import_request.id)
    verify_media_import_plan(plan, import_request)
    verify_media_import_preflight(preflight, plan)
    current_fingerprint = media_import_config_fingerprint(settings)
    if (
        plan.config_fingerprint != current_fingerprint
        or preflight.config_fingerprint != current_fingerprint
    ):
        raise AppError(
            "MEDIA_IMPORT_CONFIG_CHANGED",
            "媒体入库规划配置已变化，请创建新计划",
            status_code=409,
        )
    now = utc_now()
    inspection = MediaImportInspectionSnapshot.model_validate(
        preflight.inspection_snapshot
    )
    inspected_at = _as_utc(inspection.inspected_at)
    checked_at = _as_utc(preflight.checked_at)
    if (
        inspected_at > checked_at
        or checked_at > now
        or (now - inspected_at).total_seconds()
        > settings.media_import_preflight_max_age_seconds
    ):
        raise AppError(
            "MEDIA_IMPORT_PREFLIGHT_STALE",
            "媒体入库只读预检结果已过期，请重新预检",
            status_code=409,
        )
    if preflight.overall_status not in {PreflightStatus.PASS, PreflightStatus.WARNING}:
        raise AppError(
            "MEDIA_IMPORT_PREFLIGHT_NOT_PASSABLE",
            "媒体入库只读预检包含 BLOCKED 或 UNKNOWN，禁止批准",
            status_code=409,
        )
    if not all(
        (
            decision.acknowledges_plan_only,
            decision.acknowledges_source_retention,
            decision.acknowledges_no_overwrite,
        )
    ):
        raise AppError(
            "MEDIA_IMPORT_CONFIRMATIONS_REQUIRED",
            "必须确认仅生成计划、保留源文件且不覆盖目标",
            status_code=409,
        )

    job, media, execution, approval = await _load_bound_entities(
        session, import_request.download_job_id, for_update=True
    )
    _require_download_ready(job, execution, approval)
    current_summary = _job_summary_snapshot(job, media, execution)
    if canonical_hash(current_summary.model_dump(mode="json")) != plan.summary_snapshot_hash:
        raise AppError(
            "MEDIA_IMPORT_SUMMARY_CHANGED",
            "下载、媒体或执行摘要已变化，请创建新计划",
            status_code=409,
        )
    if job.hnr_status != HnrStatus.SATISFIED and not decision.acknowledges_hnr:
        raise AppError(
            "MEDIA_IMPORT_HNR_CONFIRMATION_REQUIRED",
            "H&R 尚未确认满足，批准规划前必须显式确认",
            status_code=409,
        )

    acknowledgements = MediaImportDecisionAcknowledgements(
        acknowledges_plan_only=True,
        acknowledges_source_retention=True,
        acknowledges_no_overwrite=True,
        acknowledges_hnr=decision.acknowledges_hnr,
    )
    previous = import_request.status
    import_request.status = MediaImportStatus.APPROVED_PLAN_ONLY
    import_request.approved_by = actor
    import_request.approved_at = now
    import_request.decision_acknowledgements = acknowledgements.model_dump(mode="json")
    import_request.updated_at = now
    _add_event(
        session,
        import_request,
        event_type="MEDIA_IMPORT_PLAN_ONLY_APPROVED",
        from_status=previous,
        actor=actor,
        details={
            "plan_hash": plan.plan_hash,
            "preflight_hash": preflight.preflight_hash,
            **acknowledgements.model_dump(mode="json"),
            "execution_authorized": False,
        },
    )
    await session.flush()
    return import_request


async def reject_media_import_request(
    session: AsyncSession,
    request_id: str,
    decision: MediaImportDecisionRequest,
    settings: Settings,
    *,
    actor: str,
) -> MediaImportRequest:
    require_media_import_enabled(settings)
    actor = _validate_actor(actor)
    import_request = await get_media_import_request(
        session, request_id, for_update=True, require_enabled=False
    )
    if import_request.status not in {
        MediaImportStatus.PREFLIGHT_REQUIRED,
        MediaImportStatus.REVIEW_REQUIRED,
    }:
        raise AppError(
            "MEDIA_IMPORT_REJECTION_STATE_INVALID",
            "媒体入库请求当前状态不能拒绝",
            status_code=409,
        )
    previous = import_request.status
    now = utc_now()
    import_request.status = MediaImportStatus.REJECTED
    import_request.rejected_by = actor
    import_request.rejected_at = now
    import_request.rejection_reason = _sanitize_reason(decision.reason)
    import_request.updated_at = now
    _add_event(
        session,
        import_request,
        event_type="MEDIA_IMPORT_REQUEST_REJECTED",
        from_status=previous,
        actor=actor,
        details={"reason": import_request.rejection_reason},
    )
    await session.flush()
    return import_request


async def revoke_media_import_request(
    session: AsyncSession,
    request_id: str,
    decision: MediaImportDecisionRequest,
    settings: Settings,
    *,
    actor: str,
) -> MediaImportRequest:
    require_media_import_enabled(settings)
    actor = _validate_actor(actor)
    import_request = await get_media_import_request(
        session, request_id, for_update=True, require_enabled=False
    )
    if import_request.status != MediaImportStatus.APPROVED_PLAN_ONLY:
        raise AppError(
            "MEDIA_IMPORT_REVOCATION_STATE_INVALID",
            "只有已批准的纯规划请求可以撤销",
            status_code=409,
        )
    previous = import_request.status
    now = utc_now()
    import_request.status = MediaImportStatus.REVOKED
    import_request.revoked_by = actor
    import_request.revoked_at = now
    import_request.revocation_reason = _sanitize_reason(decision.reason)
    import_request.updated_at = now
    _add_event(
        session,
        import_request,
        event_type="MEDIA_IMPORT_PLAN_ONLY_REVOKED",
        from_status=previous,
        actor=actor,
        details={"reason": import_request.revocation_reason},
    )
    await session.flush()
    return import_request


async def get_media_import_request(
    session: AsyncSession,
    request_id: str,
    *,
    for_update: bool = False,
    settings: Settings | None = None,
    require_enabled: bool = True,
) -> MediaImportRequest:
    if require_enabled:
        if settings is None:
            raise ValueError("settings are required when the media import gate is checked")
        require_media_import_enabled(settings)
    request = await session.get(MediaImportRequest, request_id, with_for_update=for_update)
    if request is None:
        raise AppError(
            "MEDIA_IMPORT_REQUEST_NOT_FOUND",
            "媒体入库规划请求不存在",
            status_code=404,
        )
    return request


async def list_media_import_requests(
    session: AsyncSession,
    settings: Settings,
    *,
    page: int,
    page_size: int,
    status: MediaImportStatus | None = None,
    download_job_id: str | None = None,
) -> tuple[list[MediaImportRequestSummaryResponse], int]:
    require_media_import_enabled(settings)
    filters = []
    if status is not None:
        filters.append(MediaImportRequest.status == status)
    if download_job_id is not None:
        filters.append(MediaImportRequest.download_job_id == download_job_id)
    total = int(
        await session.scalar(
            select(func.count(MediaImportRequest.id))
            .select_from(MediaImportRequest)
            .join(
                MediaImportPlan,
                MediaImportPlan.request_id == MediaImportRequest.id,
            )
            .where(*filters)
        )
        or 0
    )
    preflight_lookup = aliased(MediaImportPreflight)
    latest_preflight_id = (
        select(preflight_lookup.id)
        .where(preflight_lookup.request_id == MediaImportRequest.id)
        .order_by(preflight_lookup.created_at.desc(), preflight_lookup.id.desc())
        .limit(1)
        .correlate(MediaImportRequest)
        .scalar_subquery()
    )
    offset = (page - 1) * page_size
    rows = (
        await session.execute(
            select(
                MediaImportRequest.id.label("id"),
                MediaImportRequest.download_job_id.label("download_job_id"),
                MediaImportRequest.media_item_id.label("media_item_id"),
                MediaImportRequest.execution_id.label("execution_id"),
                MediaImportRequest.status.label("status"),
                MediaImportRequest.requested_by.label("requested_by"),
                MediaImportRequest.requested_at.label("requested_at"),
                MediaImportRequest.updated_at.label("updated_at"),
                MediaImportPlan.id.label("plan_id"),
                MediaImportPlan.mode.label("mode"),
                MediaImportPlan.proposed_operation.label("proposed_operation"),
                MediaImportPlan.plan_hash.label("plan_hash"),
                MediaImportPlan.job_summary_snapshot["media_type"]
                .as_string()
                .label("media_type"),
                MediaImportPlan.job_summary_snapshot["tmdb_id"]
                .as_integer()
                .label("tmdb_id"),
                MediaImportPlan.job_summary_snapshot["media_title"]
                .as_string()
                .label("media_title"),
                MediaImportPlan.job_summary_snapshot["media_year"]
                .as_integer()
                .label("media_year"),
                MediaImportPlan.source_manifest["source_root_ref"]
                .as_string()
                .label("source_root_ref"),
                MediaImportPlan.target_mapping["target_root_ref"]
                .as_string()
                .label("target_root_ref"),
                MediaImportPlan.job_summary_snapshot["file_count"]
                .as_integer()
                .label("file_count"),
                MediaImportPlan.job_summary_snapshot["size_bytes"]
                .as_integer()
                .label("size_bytes"),
                MediaImportPreflight.overall_status.label("preflight_status"),
                MediaImportPreflight.checked_at.label("preflight_checked_at"),
                MediaImportPreflight.preflight_hash.label("preflight_hash"),
            )
            .select_from(MediaImportRequest)
            .join(
                MediaImportPlan,
                MediaImportPlan.request_id == MediaImportRequest.id,
            )
            .outerjoin(
                MediaImportPreflight,
                MediaImportPreflight.id == latest_preflight_id,
            )
            .where(*filters)
            .order_by(MediaImportRequest.created_at.desc(), MediaImportRequest.id.desc())
            .offset(offset)
            .limit(page_size)
        )
    ).mappings().all()
    try:
        summaries = [
            MediaImportRequestSummaryResponse.model_validate(dict(row)) for row in rows
        ]
    except ValidationError as exc:
        raise AppError(
            "MEDIA_IMPORT_LIST_CONTENT_INVALID",
            "媒体入库列表摘要格式无效",
            status_code=409,
        ) from exc
    return summaries, total


async def media_import_request_response(
    session: AsyncSession,
    request: MediaImportRequest,
) -> MediaImportRequestResponse:
    plan = await _get_plan(session, request.id)
    verify_media_import_plan(plan, request)
    preflight = await session.scalar(
        select(MediaImportPreflight)
        .where(MediaImportPreflight.request_id == request.id)
        .order_by(MediaImportPreflight.created_at.desc(), MediaImportPreflight.id.desc())
        .limit(1)
    )
    preflight_response: MediaImportPreflightResponse | None = None
    if preflight is not None:
        verify_media_import_preflight(preflight, plan)
        preflight_response = _preflight_response(preflight)
    events = list(
        (
            await session.scalars(
                select(MediaImportEvent)
                .where(MediaImportEvent.request_id == request.id)
                .order_by(MediaImportEvent.created_at, MediaImportEvent.id)
            )
        ).all()
    )
    acknowledgements = (
        MediaImportDecisionAcknowledgements.model_validate(
            request.decision_acknowledgements
        )
        if request.decision_acknowledgements is not None
        else None
    )
    return MediaImportRequestResponse(
        id=request.id,
        download_job_id=request.download_job_id,
        media_item_id=request.media_item_id,
        execution_id=request.execution_id,
        status=request.status,
        requested_by=request.requested_by,
        requested_at=request.requested_at,
        approved_by=request.approved_by,
        approved_at=request.approved_at,
        rejected_by=request.rejected_by,
        rejected_at=request.rejected_at,
        rejection_reason=request.rejection_reason,
        revoked_by=request.revoked_by,
        revoked_at=request.revoked_at,
        revocation_reason=request.revocation_reason,
        decision_acknowledgements=acknowledgements,
        created_at=request.created_at,
        updated_at=request.updated_at,
        plan=_plan_response(plan),
        preflight=preflight_response,
        events=[
            MediaImportEventResponse(
                id=event.id,
                request_id=event.request_id,
                event_type=event.event_type,
                from_status=event.from_status,
                to_status=event.to_status,
                actor=event.actor,
                sanitized_details=sanitize_details(event.sanitized_details),
                created_at=event.created_at,
            )
            for event in events
        ],
    )


def verify_media_import_plan(
    plan: MediaImportPlan, request: MediaImportRequest
) -> MediaImportPlan:
    try:
        source_manifest = MediaImportSourceManifest.model_validate(plan.source_manifest)
        target_mapping = MediaImportTargetMapping.model_validate(plan.target_mapping)
        summary = MediaImportJobSummarySnapshot.model_validate(plan.job_summary_snapshot)
    except (ValidationError, ValueError) as exc:
        raise AppError(
            "MEDIA_IMPORT_PLAN_CONTENT_INVALID",
            "媒体入库规划包含无效的未受信 proposal",
            status_code=409,
        ) from exc
    if (
        plan.mode != MEDIA_IMPORT_PLAN_MODE
        or not plan.source_retention
        or plan.overwrite_allowed
        or source_manifest.source_root_ref == target_mapping.target_root_ref
    ):
        raise AppError(
            "MEDIA_IMPORT_PLAN_CONTENT_INVALID",
            "媒体入库规划违反纯规划、源保留或禁止覆盖边界",
            status_code=409,
        )
    source_payload = source_manifest.model_dump(mode="json")
    target_payload = target_mapping.model_dump(mode="json")
    summary_payload = summary.model_dump(mode="json")
    if (
        canonical_hash(source_payload) != plan.source_manifest_hash
        or canonical_hash(target_payload) != plan.target_mapping_hash
        or canonical_hash(summary_payload) != plan.summary_snapshot_hash
        or media_import_plan_hash(plan) != plan.plan_hash
    ):
        raise AppError(
            "MEDIA_IMPORT_PLAN_TAMPERED",
            "媒体入库规划完整性校验失败",
            status_code=409,
        )
    if (
        plan.request_id != request.id
        or plan.download_job_id != request.download_job_id
        or plan.media_item_id != request.media_item_id
        or plan.execution_id != request.execution_id
        or summary.download_job_id != request.download_job_id
        or summary.media_item_id != request.media_item_id
        or summary.execution_id != request.execution_id
    ):
        raise AppError(
            "MEDIA_IMPORT_PLAN_BINDING_INVALID",
            "媒体入库规划与请求、下载任务、媒体或执行绑定不一致",
            status_code=409,
        )
    _validate_mapping_sources(source_manifest, target_mapping)
    return plan


def verify_media_import_preflight(
    preflight: MediaImportPreflight, plan: MediaImportPlan
) -> MediaImportPreflight:
    try:
        inspection = MediaImportInspectionSnapshot.model_validate(
            preflight.inspection_snapshot
        )
        result = MediaImportPreflightResult.model_validate(preflight.result)
    except (ValidationError, ValueError) as exc:
        raise AppError(
            "MEDIA_IMPORT_PREFLIGHT_INVALID",
            "媒体入库只读预检记录格式无效",
            status_code=409,
        ) from exc
    inspection_payload = inspection.model_dump(mode="json")
    result_payload = result.model_dump(mode="json")
    if (
        canonical_hash(inspection_payload) != preflight.inspection_hash
        or canonical_hash(result_payload) != preflight.result_hash
        or media_import_preflight_hash(preflight) != preflight.preflight_hash
    ):
        raise AppError(
            "MEDIA_IMPORT_PREFLIGHT_TAMPERED",
            "媒体入库只读预检完整性校验失败",
            status_code=409,
        )
    if (
        preflight.request_id != plan.request_id
        or preflight.plan_id != plan.id
        or preflight.config_fingerprint != plan.config_fingerprint
        or result.config_fingerprint != preflight.config_fingerprint
        or _as_utc(result.checked_at) != _as_utc(preflight.checked_at)
        or result.overall_status != preflight.overall_status
    ):
        raise AppError(
            "MEDIA_IMPORT_PREFLIGHT_BINDING_INVALID",
            "媒体入库只读预检未绑定当前规划",
            status_code=409,
        )
    codes = [check.code for check in result.checks]
    if len(codes) != len(set(codes)) or set(codes) != set(
        MEDIA_IMPORT_PREFLIGHT_CHECK_CODES
    ):
        raise AppError(
            "MEDIA_IMPORT_PREFLIGHT_CHECKS_INVALID",
            "媒体入库只读预检检查项缺失或重复",
            status_code=409,
        )
    if _overall_status(result.checks) != result.overall_status:
        raise AppError(
            "MEDIA_IMPORT_PREFLIGHT_SUMMARY_INVALID",
            "媒体入库只读预检汇总状态与明细不一致",
            status_code=409,
        )
    _verify_inspection_proposal_binding(plan, inspection)
    return preflight


async def _load_bound_entities(
    session: AsyncSession,
    download_job_id: str,
    *,
    for_update: bool = False,
) -> tuple[DownloadJob, MediaItem, DownloadExecution, ApprovalRequest]:
    job = await session.get(DownloadJob, download_job_id, with_for_update=for_update)
    if job is None:
        raise AppError(
            "DOWNLOAD_JOB_NOT_FOUND", "下载任务不存在", status_code=404
        )
    media = await session.get(MediaItem, job.media_item_id, with_for_update=for_update)
    execution = await session.get(
        DownloadExecution, job.execution_id, with_for_update=for_update
    )
    approval = await session.get(
        ApprovalRequest, job.approval_id, with_for_update=for_update
    )
    if media is None or execution is None or approval is None:
        raise AppError(
            "MEDIA_IMPORT_BINDING_INVALID",
            "媒体入库请求缺少下载任务、媒体、审批或执行绑定",
            status_code=409,
        )
    await verify_download_execution(session, execution)
    if (
        job.media_item_id != media.id
        or job.execution_id != execution.id
        or job.approval_id != execution.approval_id
        or job.approval_id != approval.id
        or approval.media_item_id != media.id
    ):
        raise AppError(
            "MEDIA_IMPORT_BINDING_INVALID",
            "下载任务、媒体、审批与执行绑定不一致",
            status_code=409,
        )
    job_hashes = (job.info_hash_v1, job.info_hash_v2)
    execution_hashes = (execution.actual_info_hash_v1, execution.actual_info_hash_v2)
    if (
        job_hashes != execution_hashes
        or job.size_bytes != execution.actual_size_bytes
        or job.file_count != execution.actual_file_count
    ):
        raise AppError(
            "MEDIA_IMPORT_BINDING_INVALID",
            "下载任务与执行的 hash、大小或文件数绑定不一致",
            status_code=409,
        )
    return job, media, execution, approval


def _require_download_ready(
    job: DownloadJob,
    execution: DownloadExecution,
    approval: ApprovalRequest,
) -> None:
    if job.status not in _ELIGIBLE_JOB_STATUSES or job.progress < 1:
        raise AppError(
            "MEDIA_IMPORT_DOWNLOAD_NOT_READY",
            "只有进度为 100% 的做种、完成或暂停任务可以创建媒体入库规划",
            status_code=409,
        )
    if (
        execution.status not in _FINAL_EXECUTION_STATUSES
        or requires_reconciliation(execution)
        or execution.verified_at is None
        or approval.status != ApprovalStatus.CONSUMED
    ):
        raise AppError(
            "MEDIA_IMPORT_EXECUTION_NOT_FINAL",
            "下载执行尚未处于已核验且无需对账的终态",
            status_code=409,
        )


def _validate_proposal_bindings(
    job: DownloadJob, request_data: MediaImportCreateRequest
) -> None:
    files = request_data.source_manifest.files
    if len(files) != job.file_count or sum(item.size_bytes for item in files) != job.size_bytes:
        raise AppError(
            "MEDIA_IMPORT_SOURCE_PROPOSAL_MISMATCH",
            "未受信源清单 proposal 的文件数或总大小与下载任务不一致",
            status_code=409,
        )
    _validate_mapping_sources(request_data.source_manifest, request_data.target_mapping)


def _validate_mapping_sources(
    source_manifest: MediaImportSourceManifest,
    target_mapping: MediaImportTargetMapping,
) -> None:
    source_paths = {item.relative_path for item in source_manifest.files}
    unknown = [
        item.source_relative_path
        for item in target_mapping.files
        if item.source_relative_path not in source_paths
    ]
    if unknown:
        raise AppError(
            "MEDIA_IMPORT_TARGET_PROPOSAL_INVALID",
            "目标映射引用了源清单中不存在的文件",
            status_code=409,
        )


def _job_summary_snapshot(
    job: DownloadJob,
    media: MediaItem,
    execution: DownloadExecution,
) -> MediaImportJobSummarySnapshot:
    if (
        execution.actual_size_bytes is None
        or execution.actual_file_count is None
        or execution.verified_at is None
    ):
        raise AppError(
            "MEDIA_IMPORT_BINDING_INVALID",
            "下载执行缺少已核验的大小、文件数或时间绑定",
            status_code=409,
        )
    try:
        return MediaImportJobSummarySnapshot(
            download_job_id=job.id,
            media_item_id=media.id,
            execution_id=execution.id,
            approval_id=job.approval_id,
            job_status=job.status,
            progress=job.progress,
            info_hash_v1=job.info_hash_v1,
            info_hash_v2=job.info_hash_v2,
            size_bytes=job.size_bytes,
            file_count=job.file_count,
            completed_at=(
                _as_utc(job.completed_at) if job.completed_at is not None else None
            ),
            hnr_status=job.hnr_status,
            media_type=media.media_type,
            tmdb_id=media.tmdb_id,
            media_title=media.title,
            media_year=media.year,
            execution_status=execution.status,
            actual_info_hash_v1=execution.actual_info_hash_v1,
            actual_info_hash_v2=execution.actual_info_hash_v2,
            actual_size_bytes=execution.actual_size_bytes,
            actual_file_count=execution.actual_file_count,
            verified_at=_as_utc(execution.verified_at),
        )
    except ValidationError as exc:
        raise AppError(
            "MEDIA_IMPORT_BINDING_INVALID",
            "下载任务、媒体或执行摘要格式无效",
            status_code=409,
        ) from exc


def _evaluate_inspection(
    plan: MediaImportPlan,
    inspection: MediaImportInspectionSnapshot,
    *,
    current_summary: MediaImportJobSummarySnapshot,
    approval: ApprovalRequest,
    checked_at: datetime,
) -> MediaImportPreflightResult:
    source_manifest = MediaImportSourceManifest.model_validate(plan.source_manifest)
    target_mapping = MediaImportTargetMapping.model_validate(plan.target_mapping)
    checks: list[MediaImportPreflightCheck] = []

    summary_matches = (
        canonical_hash(current_summary.model_dump(mode="json"))
        == plan.summary_snapshot_hash
        and inspection.download_job_id == plan.download_job_id
        and inspection.info_hash_v1 == current_summary.info_hash_v1
        and inspection.info_hash_v2 == current_summary.info_hash_v2
        and approval.status == ApprovalStatus.CONSUMED
    )
    checks.append(
        _check(
            "SUMMARY_BINDING",
            PreflightStatus.PASS if summary_matches else PreflightStatus.BLOCKED,
            "受信检查已绑定固定下载、媒体、执行和 hash 摘要"
            if summary_matches
            else "受信检查与固定下载、媒体、执行或 hash 摘要不一致",
        )
    )
    ready = (
        current_summary.progress >= 1
        and current_summary.job_status in _ELIGIBLE_JOB_STATUSES
        and current_summary.execution_status in _FINAL_EXECUTION_STATUSES
    )
    checks.append(
        _check(
            "DOWNLOAD_READY",
            PreflightStatus.PASS if ready else PreflightStatus.BLOCKED,
            "下载任务已完成且处于允许的做种、完成或暂停状态"
            if ready
            else "下载任务尚未完成或当前状态不允许规划入库",
        )
    )

    proposed_sources = {
        item.relative_path: item.size_bytes for item in source_manifest.files
    }
    inspected_sources = {
        item.relative_path: item.size_bytes for item in inspection.source_files
    }
    source_binding = (
        inspection.source_root_ref == source_manifest.source_root_ref
        and inspected_sources == proposed_sources
    )
    checks.append(
        _check(
            "SOURCE_MANIFEST_BINDING",
            PreflightStatus.PASS if source_binding else PreflightStatus.BLOCKED,
            "受信源文件观察与未受信 proposal 精确一致"
            if source_binding
            else "受信源文件观察与未受信 proposal 不一致",
        )
    )
    source_safe = source_binding and all(
        item.exists and item.is_regular_file and not item.is_symlink
        for item in inspection.source_files
    )
    checks.append(
        _check(
            "SOURCE_FILES_SAFE",
            PreflightStatus.PASS if source_safe else PreflightStatus.BLOCKED,
            "全部源文件存在、为普通文件且不是符号链接"
            if source_safe
            else "源文件缺失、不是普通文件或包含符号链接",
        )
    )
    source_complete = source_binding and all(
        item.complete for item in inspection.source_files
    )
    checks.append(
        _check(
            "SOURCE_FILES_COMPLETE",
            PreflightStatus.PASS if source_complete else PreflightStatus.BLOCKED,
            "全部源文件只读检查均已完成"
            if source_complete
            else "至少一个源文件尚未完成",
        )
    )

    proposed_targets = {item.target_relative_path for item in target_mapping.files}
    inspected_targets = {item.relative_path for item in inspection.target_files}
    target_binding = (
        inspection.target_root_ref == target_mapping.target_root_ref
        and inspected_targets == proposed_targets
    )
    checks.append(
        _check(
            "TARGET_MAPPING_BINDING",
            PreflightStatus.PASS if target_binding else PreflightStatus.BLOCKED,
            "受信目标观察与未受信映射 proposal 精确一致"
            if target_binding
            else "受信目标观察与未受信映射 proposal 不一致",
        )
    )
    collision_free = target_binding and all(
        not item.exists for item in inspection.target_files
    )
    checks.append(
        _check(
            "TARGET_COLLISION_FREE",
            PreflightStatus.PASS if collision_free else PreflightStatus.BLOCKED,
            "目标路径均不存在，禁止覆盖策略可满足"
            if collision_free
            else "至少一个目标路径已存在，禁止覆盖",
        )
    )

    selected_sizes = {
        item.relative_path: item.size_bytes for item in source_manifest.files
    }
    required_bytes = sum(
        selected_sizes[item.source_relative_path] for item in target_mapping.files
    )
    if plan.proposed_operation == MediaImportOperation.HARDLINK:
        if inspection.same_filesystem is True:
            operation_status = PreflightStatus.PASS
            operation_message = "只读检查确认硬链接源和目标位于同一文件系统"
        elif inspection.same_filesystem is False:
            operation_status = PreflightStatus.BLOCKED
            operation_message = "硬链接源和目标不在同一文件系统"
        else:
            operation_status = PreflightStatus.UNKNOWN
            operation_message = "无法确认硬链接源和目标是否位于同一文件系统"
    elif inspection.available_bytes is None:
        operation_status = PreflightStatus.UNKNOWN
        operation_message = "无法确认复制操作所需的可用空间"
    elif inspection.available_bytes < required_bytes:
        operation_status = PreflightStatus.BLOCKED
        operation_message = "复制操作的目标可用空间不足"
    else:
        operation_status = PreflightStatus.PASS
        operation_message = "只读检查确认复制操作的目标可用空间充足"
    checks.append(_check("OPERATION_CAPABILITY", operation_status, operation_message))
    checks.append(
        _check(
            "SOURCE_RETENTION_POLICY",
            PreflightStatus.PASS,
            "规划固定保留下载源文件",
        )
    )
    checks.append(
        _check(
            "NO_OVERWRITE_POLICY",
            PreflightStatus.PASS,
            "规划固定禁止覆盖任何目标文件",
        )
    )
    hnr_status = (
        PreflightStatus.PASS
        if current_summary.hnr_status == HnrStatus.SATISFIED
        else PreflightStatus.WARNING
    )
    checks.append(
        _check(
            "HNR_DISCLOSURE",
            hnr_status,
            "H&R 状态已明确记录为 SATISFIED"
            if hnr_status == PreflightStatus.PASS
            else "H&R 状态未确认满足，人工批准时必须另行确认",
        )
    )
    return MediaImportPreflightResult(
        overall_status=_overall_status(checks),
        checked_at=checked_at,
        config_fingerprint=plan.config_fingerprint,
        checks=checks,
    )


def _verify_inspection_proposal_binding(
    plan: MediaImportPlan, inspection: MediaImportInspectionSnapshot
) -> None:
    source_manifest = MediaImportSourceManifest.model_validate(plan.source_manifest)
    target_mapping = MediaImportTargetMapping.model_validate(plan.target_mapping)
    summary = MediaImportJobSummarySnapshot.model_validate(plan.job_summary_snapshot)
    proposed_sources = {
        item.relative_path: item.size_bytes for item in source_manifest.files
    }
    inspected_sources = {
        item.relative_path: item.size_bytes for item in inspection.source_files
    }
    proposed_targets = {item.target_relative_path for item in target_mapping.files}
    inspected_targets = {item.relative_path for item in inspection.target_files}
    if (
        inspection.download_job_id != plan.download_job_id
        or inspection.info_hash_v1 != summary.info_hash_v1
        or inspection.info_hash_v2 != summary.info_hash_v2
        or inspection.source_root_ref != source_manifest.source_root_ref
        or inspection.target_root_ref != target_mapping.target_root_ref
        or inspected_sources != proposed_sources
        or inspected_targets != proposed_targets
    ):
        raise AppError(
            "MEDIA_IMPORT_INSPECTION_BINDING_INVALID",
            "受信只读检查快照未与未受信 proposal 精确绑定",
            status_code=409,
        )


def _check(
    code: str, status: PreflightStatus, message: str
) -> MediaImportPreflightCheck:
    return MediaImportPreflightCheck(code=code, status=status, message=message)


def _overall_status(checks: list[MediaImportPreflightCheck]) -> PreflightStatus:
    return max(checks, key=lambda check: _STATUS_SEVERITY[check.status]).status


def _validated_target_roots(settings: Settings) -> tuple[str, ...]:
    roots = settings.media_import_target_root_refs
    if not roots:
        raise AppError(
            "MEDIA_IMPORT_TARGETS_NOT_CONFIGURED",
            "媒体入库目标根引用白名单未配置",
            status_code=409,
        )
    try:
        validated = tuple(validate_internal_root_ref(root) for root in roots)
    except ValueError as exc:
        raise AppError(
            "MEDIA_IMPORT_TARGETS_INVALID",
            "媒体入库目标根引用白名单格式无效",
            status_code=409,
        ) from exc
    if len(set(validated)) != len(validated) or len(
        {root.casefold() for root in validated}
    ) != len(validated):
        raise AppError(
            "MEDIA_IMPORT_TARGETS_INVALID",
            "媒体入库目标根引用白名单包含重复或大小写碰撞",
            status_code=409,
        )
    return validated


async def _get_plan(session: AsyncSession, request_id: str) -> MediaImportPlan:
    plan = await session.scalar(
        select(MediaImportPlan).where(MediaImportPlan.request_id == request_id).limit(1)
    )
    if plan is None:
        raise AppError(
            "MEDIA_IMPORT_PLAN_NOT_FOUND",
            "媒体入库规划请求缺少不可变计划",
            status_code=409,
        )
    return plan


async def _get_preflight(
    session: AsyncSession, request_id: str
) -> MediaImportPreflight:
    preflight = await session.scalar(
        select(MediaImportPreflight)
        .where(MediaImportPreflight.request_id == request_id)
        .order_by(MediaImportPreflight.created_at.desc(), MediaImportPreflight.id.desc())
        .limit(1)
    )
    if preflight is None:
        raise AppError(
            "MEDIA_IMPORT_PREFLIGHT_NOT_FOUND",
            "媒体入库请求缺少受信只读预检",
            status_code=409,
        )
    return preflight


def _plan_response(plan: MediaImportPlan) -> MediaImportPlanResponse:
    return MediaImportPlanResponse(
        id=plan.id,
        request_id=plan.request_id,
        download_job_id=plan.download_job_id,
        media_item_id=plan.media_item_id,
        execution_id=plan.execution_id,
        mode=MEDIA_IMPORT_PLAN_MODE,
        proposed_operation=plan.proposed_operation,
        source_manifest=MediaImportSourceManifest.model_validate(plan.source_manifest),
        source_manifest_hash=plan.source_manifest_hash,
        target_mapping=MediaImportTargetMapping.model_validate(plan.target_mapping),
        target_mapping_hash=plan.target_mapping_hash,
        job_summary_snapshot=MediaImportJobSummarySnapshot.model_validate(
            plan.job_summary_snapshot
        ),
        summary_snapshot_hash=plan.summary_snapshot_hash,
        config_fingerprint=plan.config_fingerprint,
        plan_hash=plan.plan_hash,
        created_by=plan.created_by,
        created_at=plan.created_at,
    )


def _preflight_response(preflight: MediaImportPreflight) -> MediaImportPreflightResponse:
    return MediaImportPreflightResponse(
        id=preflight.id,
        request_id=preflight.request_id,
        plan_id=preflight.plan_id,
        overall_status=preflight.overall_status,
        inspection_hash=preflight.inspection_hash,
        result_hash=preflight.result_hash,
        preflight_hash=preflight.preflight_hash,
        config_fingerprint=preflight.config_fingerprint,
        result=MediaImportPreflightResult.model_validate(preflight.result),
        checked_by=preflight.checked_by,
        checked_at=preflight.checked_at,
        created_at=preflight.created_at,
    )


def _add_event(
    session: AsyncSession,
    request: MediaImportRequest,
    *,
    event_type: str,
    from_status: MediaImportStatus | None,
    actor: str,
    details: dict[str, object],
) -> None:
    session.add(
        MediaImportEvent(
            request_id=request.id,
            event_type=event_type,
            from_status=from_status.value if from_status is not None else None,
            to_status=request.status.value,
            actor=actor,
            sanitized_details=sanitize_details(details),
            created_at=utc_now(),
        )
    )


def _sanitize_reason(value: str | None) -> str | None:
    try:
        return normalize_media_import_reason(value)
    except ValueError as exc:
        raise AppError(
            "MEDIA_IMPORT_REASON_INVALID",
            "媒体入库决策原因包含控制字符或清理后超过存储限制",
            status_code=422,
        ) from exc


def _validate_actor(value: str) -> str:
    actor = value.strip()
    if not actor or len(actor) > 120:
        raise ValueError("actor must be between 1 and 120 characters")
    return actor


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
