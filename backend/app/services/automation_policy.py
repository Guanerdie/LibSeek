from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import sanitize_details
from app.core.time import utc_now
from app.errors import AppError
from app.models.entities import (
    AutomationDecision,
    AutomationPolicyHead,
    AutomationPolicyRevision,
    new_id,
)
from app.models.enums import AutomationMode, AutomationStage, DecisionOutcome
from app.schemas.automation import AutomationPolicyRevisionCreateRequest

DEFAULT_ELIGIBILITY_RULES: dict[str, float | int] = {
    "identity_min_score": 0.5,
    "identity_min_margin": 0.1,
    "torrent_min_score": 0.75,
    "torrent_min_margin": 0.1,
    "torrent_min_seeders": 1,
}
DEFAULT_ACKNOWLEDGEMENTS: dict[str, bool] = {
    "acknowledges_hnr": False,
    "acknowledges_seeding": False,
    "acknowledges_plan_only": False,
    "acknowledges_add_paused_only": False,
}


def policy_hash_payload(
    *,
    revision_no: int,
    identity_mode: AutomationMode,
    torrent_selection_mode: AutomationMode,
    approval_mode: AutomationMode,
    execution_mode: AutomationMode,
    eligibility_rules: dict[str, Any],
    acknowledgements: dict[str, Any],
    previous_policy_hash: str | None,
) -> dict[str, object]:
    return {
        "revision_no": revision_no,
        "identity_mode": identity_mode.value,
        "torrent_selection_mode": torrent_selection_mode.value,
        "approval_mode": approval_mode.value,
        "execution_mode": execution_mode.value,
        "eligibility_rules": eligibility_rules,
        "acknowledgements": acknowledgements,
        "previous_policy_hash": previous_policy_hash,
    }


def canonical_hash(value: dict[str, object]) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def calculate_policy_hash(revision: AutomationPolicyRevision) -> str:
    return canonical_hash(
        policy_hash_payload(
            revision_no=revision.revision_no,
            identity_mode=revision.identity_mode,
            torrent_selection_mode=revision.torrent_selection_mode,
            approval_mode=revision.approval_mode,
            execution_mode=revision.execution_mode,
            eligibility_rules=revision.eligibility_rules,
            acknowledgements=revision.acknowledgements,
            previous_policy_hash=revision.previous_policy_hash,
        )
    )


async def get_current_policy(
    session: AsyncSession, *, for_update: bool = False
) -> tuple[AutomationPolicyHead, AutomationPolicyRevision]:
    statement = select(AutomationPolicyHead).where(AutomationPolicyHead.scope == "global")
    if for_update:
        statement = statement.with_for_update()
    head = await session.scalar(statement)
    if head is None:
        try:
            async with session.begin_nested():
                head, revision = _new_default_policy()
                session.add_all((revision, head))
                await session.flush()
        except IntegrityError:
            head = await session.scalar(statement)
            if head is None:
                raise AppError(
                    "AUTOMATION_POLICY_NOT_INITIALIZED",
                    "自动化策略尚未完成初始化",
                    status_code=503,
                ) from None
        else:
            return head, revision
    current_revision = await session.get(
        AutomationPolicyRevision, head.current_revision_id
    )
    if current_revision is None:
        raise AppError(
            "AUTOMATION_POLICY_HEAD_INVALID",
            "自动化策略当前版本绑定无效",
            status_code=409,
        )
    await verify_policy_revision(session, current_revision)
    if head.version != current_revision.revision_no:
        raise AppError(
            "AUTOMATION_POLICY_HEAD_INVALID",
            "自动化策略版本指针不一致",
            status_code=409,
        )
    return head, current_revision


async def verify_policy_revision(
    session: AsyncSession, revision: AutomationPolicyRevision
) -> None:
    current = revision
    seen: set[str] = set()
    while True:
        if current.id in seen:
            raise AppError(
                "AUTOMATION_POLICY_CHAIN_INVALID",
                "自动化策略哈希链无效",
                status_code=409,
            )
        seen.add(current.id)
        if calculate_policy_hash(current) != current.policy_hash:
            raise AppError(
                "AUTOMATION_POLICY_TAMPERED",
                "自动化策略完整性校验失败",
                status_code=409,
            )
        if current.revision_no == 1:
            if current.previous_policy_hash is not None:
                raise AppError(
                    "AUTOMATION_POLICY_CHAIN_INVALID",
                    "自动化策略哈希链无效",
                    status_code=409,
                )
            return
        previous = await session.scalar(
            select(AutomationPolicyRevision).where(
                AutomationPolicyRevision.revision_no == current.revision_no - 1
            )
        )
        if previous is None or current.previous_policy_hash != previous.policy_hash:
            raise AppError(
                "AUTOMATION_POLICY_CHAIN_INVALID",
                "自动化策略哈希链无效",
                status_code=409,
            )
        current = previous


async def publish_policy_revision(
    session: AsyncSession,
    request: AutomationPolicyRevisionCreateRequest,
    *,
    actor: str,
) -> tuple[AutomationPolicyHead, AutomationPolicyRevision]:
    head, current = await get_current_policy(session, for_update=True)
    if request.base_revision_no != current.revision_no:
        raise AppError(
            "AUTOMATION_POLICY_REVISION_CONFLICT",
            "自动化策略已被其他管理员更新",
            status_code=409,
            details={"current_revision_no": current.revision_no},
        )
    _validate_acknowledgements(request)
    rules: dict[str, float | int] = {
        "identity_min_score": request.identity_min_score,
        "identity_min_margin": request.identity_min_margin,
        "torrent_min_score": request.torrent_min_score,
        "torrent_min_margin": request.torrent_min_margin,
        "torrent_min_seeders": request.torrent_min_seeders,
    }
    acknowledgements = {
        "acknowledges_hnr": request.acknowledges_hnr,
        "acknowledges_seeding": request.acknowledges_seeding,
        "acknowledges_plan_only": request.acknowledges_plan_only,
        "acknowledges_add_paused_only": request.acknowledges_add_paused_only,
    }
    revision_no = current.revision_no + 1
    revision = AutomationPolicyRevision(
        id=new_id(),
        revision_no=revision_no,
        identity_mode=request.identity_mode,
        torrent_selection_mode=request.torrent_selection_mode,
        approval_mode=request.approval_mode,
        execution_mode=request.execution_mode,
        eligibility_rules=rules,
        acknowledgements=acknowledgements,
        policy_hash="",
        previous_policy_hash=current.policy_hash,
        effective_from=utc_now(),
        created_by=actor,
    )
    revision.policy_hash = calculate_policy_hash(revision)
    session.add(revision)
    await session.flush()
    head.current_revision_id = revision.id
    head.version += 1
    await session.flush()
    return head, revision


async def list_policy_revisions(
    session: AsyncSession, *, page: int, page_size: int
) -> tuple[list[AutomationPolicyRevision], int]:
    await get_current_policy(session)
    total = await session.scalar(select(func.count()).select_from(AutomationPolicyRevision)) or 0
    revisions = list(
        (
            await session.scalars(
                select(AutomationPolicyRevision)
                .order_by(
                    AutomationPolicyRevision.created_at.desc(),
                    AutomationPolicyRevision.id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    )
    for revision in revisions:
        await verify_policy_revision(session, revision)
    return revisions, total


async def list_automation_decisions(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    stage: AutomationStage | None = None,
    outcome: DecisionOutcome | None = None,
    media_item_id: str | None = None,
) -> tuple[list[AutomationDecision], int]:
    filters = []
    if stage is not None:
        filters.append(AutomationDecision.stage == stage)
    if outcome is not None:
        filters.append(AutomationDecision.outcome == outcome)
    if media_item_id is not None:
        filters.append(AutomationDecision.media_item_id == media_item_id)
    total = (
        await session.scalar(select(func.count()).select_from(AutomationDecision).where(*filters))
        or 0
    )
    decisions = list(
        (
            await session.scalars(
                select(AutomationDecision)
                .where(*filters)
                .order_by(AutomationDecision.created_at.desc(), AutomationDecision.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    )
    for decision in decisions:
        verify_automation_decision(decision)
    return decisions, total


async def get_automation_decision(
    session: AsyncSession, decision_id: str
) -> AutomationDecision:
    decision = await session.get(AutomationDecision, decision_id)
    if decision is None:
        raise AppError(
            "AUTOMATION_DECISION_NOT_FOUND",
            "自动化决策记录不存在",
            status_code=404,
        )
    verify_automation_decision(decision)
    return decision


def calculate_decision_evidence_hash(evidence_snapshot: dict[str, Any]) -> str:
    sanitized = sanitize_details(evidence_snapshot)
    return canonical_hash(sanitized)


def calculate_decision_dedupe_key(
    *,
    policy_revision_id: str,
    stage: AutomationStage,
    action: str,
    outcome: DecisionOutcome,
    media_item_id: str,
    metadata_match_id: str | None,
    torrent_candidate_id: str | None,
    approval_request_id: str | None,
    download_execution_id: str | None,
    reason_codes: list[str],
    evidence_hash: str,
) -> str:
    # Output identifiers and evidence are integrity-bound separately. Keeping them out of
    # the dedupe subject lets concurrent workers contend on one stable action key before
    # either side has created the action's output row.
    del download_execution_id, reason_codes, evidence_hash
    return canonical_hash(
        {
            "version": 2,
            "policy_revision_id": policy_revision_id,
            "stage": stage.value,
            "action": action,
            "outcome": outcome.value,
            "media_item_id": media_item_id,
            "metadata_match_id": metadata_match_id,
            "torrent_candidate_id": torrent_candidate_id,
            "approval_request_id": approval_request_id,
        }
    )


def verify_automation_decision(decision: AutomationDecision) -> None:
    evidence_hash = calculate_decision_evidence_hash(decision.evidence_snapshot)
    if evidence_hash != decision.evidence_hash:
        raise AppError(
            "AUTOMATION_DECISION_TAMPERED",
            "自动化决策证据完整性校验失败",
            status_code=409,
        )
    dedupe_key = calculate_decision_dedupe_key(
        policy_revision_id=decision.policy_revision_id,
        stage=decision.stage,
        action=decision.action,
        outcome=decision.outcome,
        media_item_id=decision.media_item_id,
        metadata_match_id=decision.metadata_match_id,
        torrent_candidate_id=decision.torrent_candidate_id,
        approval_request_id=decision.approval_request_id,
        download_execution_id=decision.download_execution_id,
        reason_codes=decision.reason_codes,
        evidence_hash=evidence_hash,
    )
    if dedupe_key != decision.dedupe_key:
        raise AppError(
            "AUTOMATION_DECISION_BINDING_INVALID",
            "自动化决策去重绑定完整性校验失败",
            status_code=409,
        )


def _validate_acknowledgements(request: AutomationPolicyRevisionCreateRequest) -> None:
    missing: list[str] = []
    if request.approval_mode == AutomationMode.AUTO_IF_ELIGIBLE:
        for name in (
            "acknowledges_hnr",
            "acknowledges_seeding",
            "acknowledges_plan_only",
        ):
            if not getattr(request, name):
                missing.append(name)
    if request.execution_mode == AutomationMode.AUTO_IF_ELIGIBLE:
        for name in (
            "acknowledges_hnr",
            "acknowledges_seeding",
            "acknowledges_add_paused_only",
        ):
            if not getattr(request, name) and name not in missing:
                missing.append(name)
    if missing:
        raise AppError(
            "AUTOMATION_ACKNOWLEDGEMENTS_REQUIRED",
            "启用自动审批或执行前必须确认相关责任边界",
            status_code=409,
            details={"missing": missing},
        )


def _new_default_policy() -> tuple[AutomationPolicyHead, AutomationPolicyRevision]:
    now = utc_now()
    revision = AutomationPolicyRevision(
        id=new_id(),
        revision_no=1,
        identity_mode=AutomationMode.MANUAL,
        torrent_selection_mode=AutomationMode.MANUAL,
        approval_mode=AutomationMode.MANUAL,
        execution_mode=AutomationMode.MANUAL,
        eligibility_rules=dict(DEFAULT_ELIGIBILITY_RULES),
        acknowledgements=dict(DEFAULT_ACKNOWLEDGEMENTS),
        policy_hash="",
        previous_policy_hash=None,
        effective_from=now,
        created_by="system:bootstrap",
        created_at=now,
    )
    revision.policy_hash = calculate_policy_hash(revision)
    return AutomationPolicyHead(
        scope="global", current_revision_id=revision.id, version=1
    ), revision
