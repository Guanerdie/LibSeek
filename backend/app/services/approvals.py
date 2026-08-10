from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import sanitize_details
from app.core.time import utc_now
from app.errors import AppError
from app.models.entities import (
    ApprovalEvent,
    ApprovalRequest,
    DownloadPlan,
    MediaItem,
    TorrentCandidateRecord,
    TorrentSearchRun,
)
from app.models.enums import ApprovalStatus, PreflightStatus
from app.schemas.adapters import TorrentCandidate
from app.schemas.approvals import (
    ApprovalApproveRequest,
    ApprovalCandidateSnapshot,
    ApprovalCreateRequest,
    ApprovalRejectRequest,
    ApprovalRevokeRequest,
    MediaDestinationPlan,
    PreflightResult,
    PromotionSnapshot,
    validate_internal_torrent_ref,
)
from app.services.preflight import PREFLIGHT_CHECK_CODES, preflight_policy_fingerprint


def snapshot_hash(snapshot: dict[str, object]) -> str:
    canonical = json.dumps(
        snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def verify_snapshot(approval: ApprovalRequest) -> ApprovalCandidateSnapshot:
    if snapshot_hash(approval.candidate_snapshot) != approval.snapshot_hash:
        raise AppError(
            "APPROVAL_SNAPSHOT_TAMPERED",
            "审批候选快照完整性校验失败",
            status_code=409,
        )
    try:
        snapshot = ApprovalCandidateSnapshot.model_validate(approval.candidate_snapshot)
    except ValueError as exc:
        raise AppError(
            "APPROVAL_SNAPSHOT_INVALID", "审批候选快照格式无效", status_code=409
        ) from exc
    if (
        snapshot.media_item_id != approval.media_item_id
        or snapshot.torrent_candidate_id != approval.torrent_candidate_id
        or _as_utc(snapshot.requested_at) != _as_utc(approval.requested_at)
        or _as_utc(snapshot.expires_at) != _as_utc(approval.expires_at)
    ):
        raise AppError(
            "APPROVAL_SNAPSHOT_BINDING_INVALID",
            "审批候选快照与固定审批字段不一致",
            status_code=409,
        )
    return snapshot


def validate_preflight_result(value: dict[str, object]) -> PreflightResult:
    try:
        return PreflightResult.model_validate(value)
    except ValueError as exc:
        raise AppError(
            "PREFLIGHT_RESULT_INVALID", "下载预检结果完整性校验失败", status_code=409
        ) from exc


def download_plan_hash(plan: DownloadPlan) -> str:
    payload: dict[str, object] = {
        "approval_id": plan.approval_id,
        "approval_snapshot_hash": plan.approval_snapshot_hash,
        "preflight_policy_fingerprint": plan.preflight_policy_fingerprint,
        "site_id": plan.site_id,
        "torrent_ref": plan.torrent_ref,
        "expected_info_hash": plan.expected_info_hash,
        "release_title": plan.release_title,
        "save_path_ref": plan.save_path_ref,
        "category": plan.category,
        "tags": plan.tags,
        "estimated_size_bytes": plan.estimated_size_bytes,
        "media_destination_plan": plan.media_destination_plan,
        "preflight_result": plan.preflight_result,
        "warnings": plan.warnings,
    }
    return snapshot_hash(payload)


def verify_download_plan(plan: DownloadPlan, approval: ApprovalRequest) -> DownloadPlan:
    verify_snapshot(approval)
    if plan.plan_hash != download_plan_hash(plan):
        raise AppError(
            "DOWNLOAD_PLAN_TAMPERED", "下载计划完整性校验失败", status_code=409
        )
    if (
        plan.approval_id != approval.id
        or plan.approval_snapshot_hash != approval.snapshot_hash
    ):
        raise AppError(
            "DOWNLOAD_PLAN_BINDING_INVALID", "下载计划未绑定当前审批快照", status_code=409
        )
    try:
        validate_internal_torrent_ref(plan.torrent_ref)
        if not plan.torrent_ref.casefold().startswith(f"{plan.site_id.casefold()}:"):
            raise ValueError("torrent_ref does not match site_id")
        MediaDestinationPlan.model_validate(plan.media_destination_plan)
    except ValueError as exc:
        raise AppError(
            "DOWNLOAD_PLAN_CONTENT_INVALID", "下载计划包含无效字段", status_code=409
        ) from exc
    preflight = validate_preflight_result(plan.preflight_result)
    if approval.preflight_result is None:
        raise AppError(
            "DOWNLOAD_PLAN_BINDING_INVALID", "下载计划缺少审批预检绑定", status_code=409
        )
    approval_preflight = validate_preflight_result(approval.preflight_result)
    if (
        preflight.policy_fingerprint != plan.preflight_policy_fingerprint
        or preflight.model_dump(mode="json")
        != approval_preflight.model_dump(mode="json")
    ):
        raise AppError(
            "DOWNLOAD_PLAN_BINDING_INVALID", "下载计划预检策略绑定无效", status_code=409
        )
    return plan


async def create_approval_request(
    session: AsyncSession,
    candidate_id: str,
    request: ApprovalCreateRequest,
    settings: Settings,
    *,
    actor: str,
) -> ApprovalRequest:
    candidate_record = await session.get(TorrentCandidateRecord, candidate_id)
    if candidate_record is None:
        raise AppError("TORRENT_CANDIDATE_NOT_FOUND", "PT 候选不存在", status_code=404)
    run = await session.get(TorrentSearchRun, candidate_record.search_run_id)
    media = await session.get(MediaItem, run.media_id) if run is not None else None
    if run is None or media is None:
        raise AppError("TORRENT_CANDIDATE_NOT_FOUND", "PT 候选关联信息不存在", status_code=404)

    now = utc_now()
    existing = await session.scalar(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.torrent_candidate_id == candidate_id,
            ApprovalRequest.status.in_((ApprovalStatus.PENDING, ApprovalStatus.APPROVED)),
        )
        .with_for_update()
        .limit(1)
    )
    if existing is not None:
        if _is_expired(existing, now):
            _transition(
                session,
                existing,
                ApprovalStatus.EXPIRED,
                actor="system",
                event_type="EXPIRED",
                reason="审批有效期已结束",
                now=now,
            )
            await session.flush()
        else:
            raise AppError(
                "APPROVAL_REQUEST_DUPLICATE",
                "该固定候选已有有效审批请求",
                status_code=409,
            )

    ttl = request.expires_in_minutes or settings.approval_default_ttl_minutes
    if ttl > settings.approval_max_ttl_minutes:
        raise AppError("APPROVAL_TTL_TOO_LONG", "审批有效期超过系统上限", status_code=422)
    expires_at = now + timedelta(minutes=ttl)
    candidate = TorrentCandidate.model_validate(candidate_record.candidate_snapshot)
    immutable = ApprovalCandidateSnapshot(
        media_item_id=media.id,
        media_title=media.title,
        media_type=media.media_type,
        tmdb_id=media.tmdb_id,
        year=media.year,
        torrent_candidate_id=candidate_record.id,
        site_id=candidate.site_id,
        torrent_id=candidate.torrent_id,
        torrent_ref=candidate.details_ref,
        release_title=candidate.release_title,
        size_bytes=candidate.size_bytes,
        info_hash=candidate.info_hash,
        season=candidate.season,
        episodes=candidate.episodes,
        resolution=candidate.resolution,
        source=candidate.source,
        subtitles=candidate.subtitles,
        seeders=candidate.seeders,
        promotion=PromotionSnapshot(
            download_factor=candidate.download_factor,
            upload_factor=candidate.upload_factor,
        ),
        hit_and_run=candidate.hit_and_run,
        match_score=candidate.match_score or candidate_record.match_score,
        match_reasons=list(candidate_record.match_reasons),
        warnings=list(candidate_record.warnings),
        requested_at=now,
        expires_at=expires_at,
    )
    immutable_data: dict[str, object] = immutable.model_dump(mode="json")
    digest = snapshot_hash(immutable_data)
    approval = ApprovalRequest(
        media_item_id=media.id,
        torrent_candidate_id=candidate_record.id,
        status=ApprovalStatus.PENDING,
        candidate_snapshot=immutable_data,
        snapshot_hash=digest,
        requested_by=actor,
        requested_at=now,
        expires_at=expires_at,
    )
    session.add(approval)
    await session.flush()
    _add_event(
        session,
        approval,
        event_type="REQUESTED",
        from_status=None,
        to_status=ApprovalStatus.PENDING,
        actor=actor,
        details={"expires_at": expires_at.isoformat()},
    )
    await session.flush()
    return approval


async def get_approval_or_404(
    session: AsyncSession, approval_id: str, *, for_update: bool = False
) -> ApprovalRequest:
    approval = await session.get(ApprovalRequest, approval_id, with_for_update=for_update)
    if approval is None:
        raise AppError("APPROVAL_REQUEST_NOT_FOUND", "审批请求不存在", status_code=404)
    verify_snapshot(approval)
    return approval


async def list_approval_requests(
    session: AsyncSession,
    *,
    candidate_id: str | None = None,
    limit: int = 100,
) -> list[ApprovalRequest]:
    statement = select(ApprovalRequest)
    if candidate_id:
        statement = statement.where(ApprovalRequest.torrent_candidate_id == candidate_id)
    statement = statement.order_by(ApprovalRequest.created_at.desc()).limit(limit)
    approvals = list((await session.scalars(statement)).all())
    for approval in approvals:
        verify_snapshot(approval)
    return approvals


async def list_approval_events(
    session: AsyncSession, approval_id: str
) -> list[ApprovalEvent]:
    statement = (
        select(ApprovalEvent)
        .where(ApprovalEvent.approval_request_id == approval_id)
        .order_by(ApprovalEvent.created_at.asc())
    )
    return list((await session.scalars(statement)).all())


def effective_status(approval: ApprovalRequest, now: datetime | None = None) -> ApprovalStatus:
    current = now or utc_now()
    if approval.status in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED} and _is_expired(
        approval, current
    ):
        return ApprovalStatus.EXPIRED
    return approval.status


def require_pending_approval(
    session: AsyncSession,
    approval: ApprovalRequest,
    now: datetime | None = None,
) -> ApprovalCandidateSnapshot:
    """Validate a pending approval and stage an audited expiry transition when needed.

    The caller owns the transaction. API callers must commit the staged transition before
    returning ``APPROVAL_EXPIRED`` so the state and audit event are not rolled back with the
    exceptional request path.
    """
    return _expire_or_require_pending(session, approval, now or utc_now())


async def save_preflight_result(
    session: AsyncSession,
    approval: ApprovalRequest,
    result: PreflightResult,
    *,
    actor: str,
) -> ApprovalRequest:
    require_pending_approval(session, approval)
    approval.preflight_result = result.model_dump(mode="json")
    approval.preflight_checked_at = result.checked_at
    _add_event(
        session,
        approval,
        event_type="PREFLIGHT_COMPLETED",
        from_status=approval.status,
        to_status=approval.status,
        actor=actor.strip(),
        details={
            "overall_status": result.overall_status.value,
            "check_count": len(result.checks),
        },
    )
    await session.flush()
    return approval


async def approve_request(
    session: AsyncSession,
    approval: ApprovalRequest,
    request: ApprovalApproveRequest,
    settings: Settings,
    *,
    actor: str,
) -> tuple[ApprovalRequest, DownloadPlan]:
    now = utc_now()
    snapshot = require_pending_approval(session, approval, now)
    if not all(
        (
            request.acknowledges_hnr,
            request.acknowledges_seeding,
            request.acknowledges_plan_only,
        )
    ):
        raise AppError(
            "APPROVAL_ACKNOWLEDGEMENTS_REQUIRED",
            "批准前必须确认 H&R、继续做种和仅生成计划三项声明",
            status_code=422,
        )
    if approval.preflight_result is None or approval.preflight_checked_at is None:
        raise AppError("PREFLIGHT_REQUIRED", "批准前必须完成下载预检", status_code=409)
    preflight = validate_preflight_result(approval.preflight_result)
    actual_codes = [check.code for check in preflight.checks]
    if len(actual_codes) != len(PREFLIGHT_CHECK_CODES) or set(actual_codes) != set(
        PREFLIGHT_CHECK_CODES
    ):
        raise AppError(
            "PREFLIGHT_CHECKS_INCOMPLETE",
            "下载预检缺少必需检查或包含重复检查",
            status_code=409,
        )
    checked_at = _as_utc(approval.preflight_checked_at)
    if checked_at != _as_utc(preflight.checked_at) or checked_at > _as_utc(now):
        raise AppError(
            "PREFLIGHT_TIMESTAMP_INVALID", "下载预检时间戳无效", status_code=409
        )
    if (now - checked_at).total_seconds() > settings.approval_preflight_max_age_seconds:
        raise AppError("PREFLIGHT_STALE", "下载预检结果已过期，请重新预检", status_code=409)
    if preflight.policy_fingerprint != preflight_policy_fingerprint(settings):
        raise AppError(
            "PREFLIGHT_CONFIG_CHANGED",
            "下载预检策略配置已变化，请重新预检",
            status_code=409,
        )
    if preflight.overall_status in {PreflightStatus.BLOCKED, PreflightStatus.UNKNOWN}:
        raise AppError(
            "PREFLIGHT_NOT_PASSABLE",
            "预检包含 BLOCKED 或 UNKNOWN，禁止批准",
            status_code=409,
            details={"overall_status": preflight.overall_status.value},
        )
    if not settings.qb_target_category:
        raise AppError("DOWNLOAD_PLAN_CONFIG_MISSING", "下载计划目标分类未配置", status_code=409)

    destination = MediaDestinationPlan(
        mode="PLAN_ONLY_NO_FILE_OPERATION",
        media_item_id=snapshot.media_item_id,
        media_type=snapshot.media_type,
        tmdb_id=snapshot.tmdb_id,
        season=snapshot.season,
        episodes=snapshot.episodes,
    )
    _transition(
        session,
        approval,
        ApprovalStatus.APPROVED,
        actor=actor,
        event_type="APPROVED",
        reason=None,
        now=now,
        details={
            "acknowledges_hnr": True,
            "acknowledges_seeding": True,
            "acknowledges_plan_only": True,
        },
    )
    warning_codes = [
        check.code for check in preflight.checks if check.status == PreflightStatus.WARNING
    ]
    plan = DownloadPlan(
        approval_id=approval.id,
        approval_snapshot_hash=approval.snapshot_hash,
        preflight_policy_fingerprint=preflight.policy_fingerprint,
        plan_hash="",
        site_id=snapshot.site_id,
        torrent_ref=snapshot.torrent_ref,
        expected_info_hash=snapshot.info_hash,
        release_title=snapshot.release_title,
        save_path_ref=settings.qb_save_path_ref,
        category=settings.qb_target_category,
        tags=list(settings.qb_plan_tags),
        estimated_size_bytes=snapshot.size_bytes,
        media_destination_plan=destination.model_dump(mode="json"),
        preflight_result=preflight.model_dump(mode="json"),
        warnings=list(dict.fromkeys([*snapshot.warnings, *warning_codes])),
    )
    plan.plan_hash = download_plan_hash(plan)
    session.add(plan)
    _add_event(
        session,
        approval,
        event_type="DOWNLOAD_PLAN_CREATED",
        from_status=ApprovalStatus.APPROVED,
        to_status=ApprovalStatus.APPROVED,
        actor=actor,
        details={
            "plan_hash": plan.plan_hash,
            "policy_fingerprint": plan.preflight_policy_fingerprint,
        },
    )
    await session.flush()
    return approval, plan


async def reject_request(
    session: AsyncSession,
    approval: ApprovalRequest,
    request: ApprovalRejectRequest,
    *,
    actor: str,
) -> ApprovalRequest:
    now = utc_now()
    require_pending_approval(session, approval, now)
    _transition(
        session,
        approval,
        ApprovalStatus.REJECTED,
        actor=actor,
        event_type="REJECTED",
        reason=request.reason.strip() if request.reason else None,
        now=now,
    )
    await session.flush()
    return approval


async def revoke_request(
    session: AsyncSession,
    approval: ApprovalRequest,
    request: ApprovalRevokeRequest,
    *,
    actor: str,
) -> ApprovalRequest:
    now = utc_now()
    snapshot = verify_snapshot(approval)
    del snapshot
    if approval.status == ApprovalStatus.EXPIRED or _stage_expiration_if_needed(
        session, approval, now
    ):
        raise AppError("APPROVAL_EXPIRED", "审批已过期", status_code=409)
    if approval.status != ApprovalStatus.APPROVED:
        raise AppError("APPROVAL_NOT_REVOCABLE", "只有已批准审批可以撤销", status_code=409)
    _transition(
        session,
        approval,
        ApprovalStatus.REVOKED,
        actor=actor,
        event_type="REVOKED",
        reason=request.reason.strip() if request.reason else None,
        now=now,
    )
    await session.flush()
    return approval


async def consume_approval(
    session: AsyncSession,
    approval: ApprovalRequest,
    *,
    actor: str,
) -> ApprovalRequest:
    locked = await session.scalar(
        select(ApprovalRequest)
        .where(ApprovalRequest.id == approval.id)
        .with_for_update()
        .limit(1)
    )
    if locked is None:
        raise AppError("APPROVAL_REQUEST_NOT_FOUND", "审批请求不存在", status_code=404)
    approval = locked
    now = utc_now()
    verify_snapshot(approval)
    if approval.status == ApprovalStatus.CONSUMED:
        raise AppError("APPROVAL_ALREADY_CONSUMED", "审批已经消费，禁止重复执行", status_code=409)
    if approval.status == ApprovalStatus.EXPIRED or _stage_expiration_if_needed(
        session, approval, now
    ):
        raise AppError("APPROVAL_EXPIRED", "审批已过期", status_code=409)
    if approval.status != ApprovalStatus.APPROVED:
        raise AppError("APPROVAL_NOT_CONSUMABLE", "审批当前状态不可消费", status_code=409)
    plan = await session.scalar(select(DownloadPlan).where(DownloadPlan.approval_id == approval.id))
    if plan is None:
        raise AppError("DOWNLOAD_PLAN_NOT_FOUND", "审批没有对应下载计划", status_code=409)
    verify_download_plan(plan, approval)
    _transition(
        session,
        approval,
        ApprovalStatus.CONSUMED,
        actor=actor.strip(),
        event_type="CONSUMED",
        reason=None,
        now=now,
    )
    await session.flush()
    return approval


async def get_download_plan(session: AsyncSession, approval_id: str) -> DownloadPlan:
    approval = await session.get(ApprovalRequest, approval_id)
    if approval is None:
        raise AppError("APPROVAL_REQUEST_NOT_FOUND", "审批请求不存在", status_code=404)
    plan = await session.scalar(select(DownloadPlan).where(DownloadPlan.approval_id == approval_id))
    if plan is None:
        raise AppError("DOWNLOAD_PLAN_NOT_FOUND", "下载计划尚未生成", status_code=404)
    return verify_download_plan(plan, approval)


def _expire_or_require_pending(
    session: AsyncSession, approval: ApprovalRequest, now: datetime
) -> ApprovalCandidateSnapshot:
    snapshot = verify_snapshot(approval)
    if approval.status == ApprovalStatus.EXPIRED or _stage_expiration_if_needed(
        session, approval, now
    ):
        raise AppError("APPROVAL_EXPIRED", "审批已过期", status_code=409)
    if approval.status != ApprovalStatus.PENDING:
        raise AppError("APPROVAL_NOT_PENDING", "审批当前状态不是 PENDING", status_code=409)
    return snapshot


def _stage_expiration_if_needed(
    session: AsyncSession,
    approval: ApprovalRequest,
    now: datetime,
) -> bool:
    if approval.status not in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED}:
        return False
    if not _is_expired(approval, now):
        return False
    _transition(
        session,
        approval,
        ApprovalStatus.EXPIRED,
        actor="system",
        event_type="EXPIRED",
        reason="审批有效期已结束",
        now=now,
    )
    return True


def _transition(
    session: AsyncSession,
    approval: ApprovalRequest,
    to_status: ApprovalStatus,
    *,
    actor: str,
    event_type: str,
    reason: str | None,
    now: datetime,
    details: dict[str, object] | None = None,
) -> None:
    previous = approval.status
    approval.status = to_status
    if to_status != ApprovalStatus.PENDING:
        approval.decided_at = now
    _add_event(
        session,
        approval,
        event_type=event_type,
        from_status=previous,
        to_status=to_status,
        actor=actor,
        reason=reason,
        details=details,
    )


def _add_event(
    session: AsyncSession,
    approval: ApprovalRequest,
    *,
    event_type: str,
    from_status: ApprovalStatus | None,
    to_status: ApprovalStatus,
    actor: str,
    reason: str | None = None,
    details: dict[str, object] | None = None,
) -> None:
    session.add(
        ApprovalEvent(
            approval_request_id=approval.id,
            event_type=event_type,
            from_status=from_status.value if from_status else None,
            to_status=to_status.value,
            actor=actor,
            reason=reason,
            snapshot_hash=approval.snapshot_hash,
            sanitized_details=sanitize_details(details or {}),
        )
    )


def _is_expired(approval: ApprovalRequest, now: datetime) -> bool:
    return _as_utc(approval.expires_at) <= _as_utc(now)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
