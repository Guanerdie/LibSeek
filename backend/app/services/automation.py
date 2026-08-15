from __future__ import annotations

import re
import secrets
import unicodedata
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.pt_sites.catalog import build_pt_site_catalog
from app.core.config import Settings
from app.core.pt_site_rules import effective_hnr_rule, hnr_acknowledgement_required
from app.core.security import sanitize_details
from app.errors import AppError
from app.models.entities import (
    ApprovalRequest,
    AutomationDecision,
    AutomationPolicyRevision,
    DownloadExecution,
    DownloadPlan,
    IdentityReview,
    Job,
    MediaItem,
    MetadataMatch,
    TorrentCandidateRecord,
    TorrentSearchRun,
)
from app.models.enums import (
    ApprovalStatus,
    AutomationMode,
    AutomationStage,
    DecisionOutcome,
    DownloadLaunchMode,
    JobStatus,
    MediaType,
    Origin,
    PreflightStatus,
)
from app.schemas.adapters import MetadataRecord, TorrentCandidate
from app.schemas.approvals import ApprovalCreateRequest
from app.schemas.entities import IdentityConfirmationRequest, TorrentSearchCreateRequest
from app.schemas.executions import (
    DownloadExecutionCreateRequest,
    ExecutionIntentCreateRequest,
)
from app.services.approvals import (
    approve_request_automatically,
    create_approval_request,
    validate_preflight_result,
    verify_snapshot,
)
from app.services.automation_capability import (
    automation_preflight_capability_fingerprint,
    automation_preflight_is_ready,
)
from app.services.automation_policy import (
    calculate_decision_dedupe_key,
    canonical_hash,
    get_current_policy,
    verify_automation_decision,
)
from app.services.execution_capability import download_executor_is_ready
from app.services.executions import (
    create_execution_intent,
    execute_approved_plan,
    verify_download_execution,
)

_INFO_HASH = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")
_REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,79}$")


@dataclass(frozen=True)
class EligibilityResult:
    eligible: bool
    reason_codes: tuple[str, ...]
    evidence: dict[str, Any]
    selected_id: str | None = None


def stage_mode(
    revision: AutomationPolicyRevision, stage: AutomationStage
) -> AutomationMode:
    return {
        AutomationStage.IDENTITY: revision.identity_mode,
        AutomationStage.TORRENT_SELECTION: revision.torrent_selection_mode,
        AutomationStage.APPROVAL: revision.approval_mode,
        AutomationStage.EXECUTION: revision.execution_mode,
    }[stage]


async def require_stage_not_disabled(
    session: AsyncSession, stage: AutomationStage
) -> AutomationPolicyRevision:
    _, revision = await get_current_policy(session, for_update=True)
    if stage_mode(revision, stage) == AutomationMode.DISABLED:
        raise AppError(
            "AUTOMATION_STAGE_DISABLED",
            "当前自动化策略已禁用该阶段的正向操作",
            status_code=409,
            details={"stage": stage.value, "revision_no": revision.revision_no},
        )
    return revision


def subject_is_new_for_revision(
    created_at: datetime, revision: AutomationPolicyRevision
) -> bool:
    return _as_utc(created_at) >= _as_utc(revision.effective_from)


def evaluate_identity_candidates(
    media: MediaItem,
    matches: Iterable[MetadataMatch],
    revision: AutomationPolicyRevision,
    *,
    resolution_job_id: str,
    input_fingerprint_matches: bool,
) -> EligibilityResult:
    ranked = sorted(matches, key=lambda item: (item.rank, item.id))
    evidence: dict[str, Any] = {
        "resolution_job_id": resolution_job_id,
        "candidate_count": len(ranked),
        "input_fingerprint_matches": input_fingerprint_matches,
        "source_has_tmdb_id": media.tmdb_id is not None,
    }
    reasons: list[str] = []
    if not input_fingerprint_matches:
        reasons.append("IDENTITY_INPUT_STALE")
    if not ranked:
        reasons.append("IDENTITY_CANDIDATES_EMPTY")
        return EligibilityResult(False, tuple(reasons), evidence)
    if any(match.resolution_job_id != resolution_job_id for match in ranked):
        reasons.append("IDENTITY_RESOLUTION_JOB_MISMATCH")

    top = ranked[0]
    runner_score = ranked[1].score if len(ranked) > 1 else 0.0
    margin = top.score - runner_score
    evidence.update(
        {
            "selected_metadata_match_id": top.id,
            "top_score": top.score,
            "runner_score": runner_score,
            "score_margin": margin,
            "match_reasons": list(top.match_reasons),
            "conflicts": list(top.conflicts),
        }
    )
    min_score = float(revision.eligibility_rules["identity_min_score"])
    min_margin = float(revision.eligibility_rules["identity_min_margin"])
    if top.score < min_score:
        reasons.append("IDENTITY_SCORE_TOO_LOW")
    if margin < min_margin:
        reasons.append("IDENTITY_MARGIN_TOO_SMALL")
    if top.conflicts:
        reasons.append("IDENTITY_CONFLICTS_PRESENT")

    try:
        candidate = MetadataRecord.model_validate(top.candidate_snapshot)
    except ValueError:
        reasons.append("IDENTITY_CANDIDATE_INVALID")
        return EligibilityResult(False, tuple(dict.fromkeys(reasons)), evidence, top.id)
    if candidate.media_type != media.media_type:
        reasons.append("IDENTITY_MEDIA_TYPE_MISMATCH")
    required_reasons = {"MEDIA_TYPE_MATCH"}
    if media.tmdb_id is not None:
        required_reasons.add("TMDB_ID_EXACT")
        if candidate.tmdb_id != media.tmdb_id:
            reasons.append("IDENTITY_TMDB_ID_MISMATCH")
    else:
        required_reasons.update({"TITLE_EXACT", "YEAR_MATCH"})
        title_exact = _metadata_title_exact(media, candidate)
        evidence["title_exact_verified"] = title_exact
        if "TITLE_EXACT" in top.match_reasons and not title_exact:
            reasons.append("IDENTITY_TITLE_EVIDENCE_MISMATCH")
        if not title_exact:
            reasons.append("IDENTITY_TITLE_NOT_EXACT")
        if media.year is None or candidate.year is None:
            reasons.append("IDENTITY_YEAR_UNKNOWN")
        elif media.year != candidate.year:
            reasons.append("IDENTITY_YEAR_MISMATCH")
    if not required_reasons.issubset(set(top.match_reasons)):
        reasons.append("IDENTITY_REQUIRED_EVIDENCE_MISSING")

    unique_reasons = tuple(dict.fromkeys(reasons))
    return EligibilityResult(not unique_reasons, unique_reasons, evidence, top.id)


def evaluate_torrent_candidates(
    media: MediaItem,
    candidates: Iterable[TorrentCandidateRecord],
    revision: AutomationPolicyRevision,
    *,
    search_job_id: str,
    site_id: str,
    max_size_bytes: int | None,
    confirmed_identity: MetadataRecord | None = None,
) -> EligibilityResult:
    ranked = sorted(candidates, key=lambda item: (-item.match_score, item.id))
    evidence: dict[str, Any] = {
        "search_job_id": search_job_id,
        "site_id": site_id,
        "candidate_count": len(ranked),
        "confirmed_identity_present": confirmed_identity is not None,
    }
    reasons: list[str] = []
    if site_id.casefold() != "avistaz":
        reasons.append("TORRENT_SITE_NOT_AUTOMATABLE")
    if not ranked:
        reasons.append("TORRENT_CANDIDATES_EMPTY")
        return EligibilityResult(False, tuple(reasons), evidence)

    top = ranked[0]
    runner_score = ranked[1].match_score if len(ranked) > 1 else 0.0
    margin = top.match_score - runner_score
    evidence.update(
        {
            "selected_torrent_candidate_id": top.id,
            "top_score": top.match_score,
            "runner_score": runner_score,
            "score_margin": margin,
            "match_reasons": list(top.match_reasons),
            "warnings": list(top.warnings),
        }
    )
    if top.match_score < float(revision.eligibility_rules["torrent_min_score"]):
        reasons.append("TORRENT_SCORE_TOO_LOW")
    if margin < float(revision.eligibility_rules["torrent_min_margin"]):
        reasons.append("TORRENT_MARGIN_TOO_SMALL")
    blocking_warnings = [
        warning
        for warning in top.warnings
        if not (top.site_id.casefold() == "avistaz" and warning == "HNR_UNKNOWN")
    ]
    if blocking_warnings:
        reasons.append("TORRENT_WARNINGS_PRESENT")

    try:
        candidate = TorrentCandidate.model_validate(top.candidate_snapshot)
    except ValueError:
        reasons.append("TORRENT_CANDIDATE_INVALID")
        return EligibilityResult(False, tuple(dict.fromkeys(reasons)), evidence, top.id)
    if candidate.site_id.casefold() != "avistaz" or candidate.site_id != site_id:
        reasons.append("TORRENT_SITE_BINDING_MISMATCH")
    if candidate.media_type != media.media_type:
        reasons.append("TORRENT_MEDIA_TYPE_MISMATCH")
    tmdb_exact = media.tmdb_id is not None and candidate.tmdb_id == media.tmdb_id
    imdb_exact = bool(
        confirmed_identity is not None
        and confirmed_identity.imdb_id
        and candidate.imdb_id
        and candidate.imdb_id.casefold() == confirmed_identity.imdb_id.casefold()
    )
    evidence.update({"tmdb_id_exact_verified": tmdb_exact, "imdb_id_exact_verified": imdb_exact})
    reason_tmdb_exact = "TMDB_ID_EXACT" in top.match_reasons
    reason_imdb_exact = "IMDB_ID_EXACT" in top.match_reasons
    if (reason_tmdb_exact and not tmdb_exact) or (reason_imdb_exact and not imdb_exact):
        reasons.append("TORRENT_EXTERNAL_ID_EVIDENCE_MISMATCH")
    if reason_imdb_exact and not reason_tmdb_exact:
        reasons.append("TORRENT_IMDB_ONLY_REQUIRES_MANUAL")
    if not (reason_tmdb_exact and tmdb_exact):
        reasons.append("TORRENT_EXTERNAL_ID_NOT_EXACT")
    if media.year is not None and candidate.year != media.year:
        reasons.append("TORRENT_YEAR_NOT_EXACT")
    if not effective_hnr_rule(candidate.site_id, candidate.hit_and_run).known:
        reasons.append("HNR_UNKNOWN")
    if candidate.info_hash is None or not _INFO_HASH.fullmatch(candidate.info_hash):
        reasons.append("TORRENT_INFO_HASH_UNKNOWN")
    if candidate.size_bytes is None or candidate.size_bytes <= 0:
        reasons.append("TORRENT_SIZE_UNKNOWN")
    elif max_size_bytes is not None and candidate.size_bytes > max_size_bytes:
        reasons.append("TORRENT_SIZE_LIMIT_EXCEEDED")
    min_seeders = int(revision.eligibility_rules["torrent_min_seeders"])
    if candidate.seeders is None or candidate.seeders < min_seeders:
        reasons.append("TORRENT_SEEDERS_INSUFFICIENT")
    if media.media_type == MediaType.TV:
        if not media.missing_episodes:
            reasons.append("TV_MISSING_EPISODES_UNKNOWN")
        elif not {"EPISODE_COVERAGE_EXACT", "EPISODE_COVERAGE_COMPLETE"}.intersection(
            top.match_reasons
        ):
            reasons.append("TV_EPISODE_COVERAGE_UNSAFE")

    unique_reasons = tuple(dict.fromkeys(reasons))
    return EligibilityResult(not unique_reasons, unique_reasons, evidence, top.id)


def build_automation_decision(
    revision: AutomationPolicyRevision,
    *,
    stage: AutomationStage,
    action: str,
    outcome: DecisionOutcome,
    media_item_id: str,
    reason_codes: Iterable[str],
    evidence: dict[str, Any],
    metadata_match_id: str | None = None,
    torrent_candidate_id: str | None = None,
    approval_request_id: str | None = None,
    download_execution_id: str | None = None,
) -> AutomationDecision:
    safe_evidence = sanitize_details(evidence)
    if not isinstance(safe_evidence, dict):
        raise ValueError("automation evidence must remain a mapping after sanitization")
    evidence_hash = canonical_hash(safe_evidence)
    unique_reasons = list(dict.fromkeys(reason_codes))
    if not unique_reasons or any(
        not isinstance(reason, str) or _REASON_CODE.fullmatch(reason) is None
        for reason in unique_reasons
    ):
        raise ValueError("automation reason codes must use stable uppercase identifiers")
    dedupe_key = calculate_decision_dedupe_key(
        policy_revision_id=revision.id,
        stage=stage,
        action=action,
        outcome=outcome,
        media_item_id=media_item_id,
        metadata_match_id=metadata_match_id,
        torrent_candidate_id=torrent_candidate_id,
        approval_request_id=approval_request_id,
        download_execution_id=download_execution_id,
        reason_codes=unique_reasons,
        evidence_hash=evidence_hash,
    )
    return AutomationDecision(
        id=str(uuid.uuid4()),
        policy_revision_id=revision.id,
        stage=stage,
        action=action,
        outcome=outcome,
        media_item_id=media_item_id,
        metadata_match_id=metadata_match_id,
        torrent_candidate_id=torrent_candidate_id,
        approval_request_id=approval_request_id,
        download_execution_id=download_execution_id,
        reason_codes=unique_reasons,
        evidence_snapshot=safe_evidence,
        evidence_hash=evidence_hash,
        dedupe_key=dedupe_key,
        actor="system:automation",
    )


async def add_decision_once(
    session: AsyncSession, decision: AutomationDecision
) -> tuple[AutomationDecision, bool]:
    existing = await session.scalar(
        select(AutomationDecision)
        .where(AutomationDecision.dedupe_key == decision.dedupe_key)
        .limit(1)
    )
    if existing is not None:
        return existing, False
    try:
        async with session.begin_nested():
            session.add(decision)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(
            select(AutomationDecision)
            .where(AutomationDecision.dedupe_key == decision.dedupe_key)
            .limit(1)
        )
        if existing is None:
            raise
        return existing, False
    return decision, True


async def maybe_automate_identity(
    session: AsyncSession,
    *,
    job: Job,
    media: MediaItem,
    settings: Settings,
) -> IdentityReview | None:
    _, revision = await get_current_policy(session, for_update=True)
    mode_outcome = _mode_outcome(
        revision,
        AutomationStage.IDENTITY,
        subject_created_at=job.created_at,
        engine_enabled=settings.enable_automation_engine,
    )
    matches = list(
        (
            await session.scalars(
                select(MetadataMatch)
                .where(
                    MetadataMatch.media_id == media.id,
                    MetadataMatch.resolution_job_id == job.id,
                )
                .order_by(MetadataMatch.rank, MetadataMatch.id)
            )
        ).all()
    )
    evidence = {
        "resolution_job_id": job.id,
        "candidate_count": len(matches),
        "input_fingerprint_present": isinstance(
            job.payload.get("input_fingerprint"), str
        ),
    }
    if mode_outcome is not None:
        outcome, reasons = mode_outcome
        await _record_stage_decision(
            session,
            revision,
            stage=AutomationStage.IDENTITY,
            action="CONFIRM_IDENTITY",
            outcome=outcome,
            media_item_id=media.id,
            reasons=reasons,
            evidence=evidence,
        )
        return None

    from app.services.workflow import (
        confirm_identity,
        metadata_resolution_input_fingerprint,
    )

    eligibility = evaluate_identity_candidates(
        media,
        matches,
        revision,
        resolution_job_id=job.id,
        input_fingerprint_matches=(
            job.payload.get("input_fingerprint")
            == metadata_resolution_input_fingerprint(media)
        ),
    )
    if not eligibility.eligible or eligibility.selected_id is None:
        await _record_stage_decision(
            session,
            revision,
            stage=AutomationStage.IDENTITY,
            action="CONFIRM_IDENTITY",
            outcome=DecisionOutcome.MANUAL_REQUIRED,
            media_item_id=media.id,
            metadata_match_id=eligibility.selected_id,
            reasons=eligibility.reason_codes,
            evidence=eligibility.evidence,
        )
        return None

    decision = build_automation_decision(
        revision,
        stage=AutomationStage.IDENTITY,
        action="CONFIRM_IDENTITY",
        outcome=DecisionOutcome.ACTION_CREATED,
        media_item_id=media.id,
        metadata_match_id=eligibility.selected_id,
        reason_codes=("IDENTITY_ELIGIBLE",),
        evidence=eligibility.evidence,
    )
    _, created = await add_decision_once(session, decision)
    if not created:
        return None
    review = await confirm_identity(
        session,
        media,
        IdentityConfirmationRequest(metadata_match_id=eligibility.selected_id),
        actor=_automation_actor(revision),
        audit_event_type="IDENTITY_CONFIRMED_AUTOMATICALLY",
    )
    await maybe_automate_torrent_search_after_identity(
        session,
        media=media,
        trigger_created_at=review.created_at,
        settings=settings,
    )
    return review


async def maybe_automate_torrent_search_after_identity(
    session: AsyncSession,
    *,
    media: MediaItem,
    trigger_created_at: datetime,
    settings: Settings,
) -> Job | None:
    del trigger_created_at
    _, revision = await get_current_policy(session, for_update=True)
    if (
        stage_mode(revision, AutomationStage.TORRENT_SELECTION)
        == AutomationMode.DISABLED
    ):
        return None

    catalog = build_pt_site_catalog(settings)
    site_id = next(
        (
            registered_site_id
            for registered_site_id in catalog.registered_site_ids
            if (declaration := catalog.get(registered_site_id)) is not None
            and declaration.available_for_search
            and media.media_type in declaration.media_types
        ),
        None,
    )
    if site_id is None:
        return None

    from app.services.workflow import enqueue_torrent_search

    _, job, _ = await enqueue_torrent_search(
        session,
        media,
        TorrentSearchCreateRequest(site_id=site_id),
        max_attempts=settings.job_max_attempts,
        settings=settings,
    )
    job.payload = {
        key: value
        for key, value in job.payload.items()
        if key not in {"automation_policy_revision_id", "automation_decision_id"}
    }
    await session.flush()
    return job


async def maybe_automate_torrent_selection(
    session: AsyncSession,
    *,
    job: Job,
    run: TorrentSearchRun,
    media: MediaItem,
    settings: Settings,
) -> ApprovalRequest | None:
    _, revision = await get_current_policy(session, for_update=True)
    mode_outcome = _mode_outcome(
        revision,
        AutomationStage.TORRENT_SELECTION,
        subject_created_at=job.created_at,
        engine_enabled=settings.enable_automation_engine,
    )
    candidates = list(
        (
            await session.scalars(
                select(TorrentCandidateRecord)
                .where(TorrentCandidateRecord.search_run_id == run.id)
                .order_by(
                    TorrentCandidateRecord.match_score.desc(),
                    TorrentCandidateRecord.id,
                )
            )
        ).all()
    )
    evidence = {
        "search_job_id": job.id,
        "search_run_id": run.id,
        "site_id": run.site_id,
        "candidate_count": len(candidates),
    }
    if mode_outcome is not None:
        outcome, reasons = mode_outcome
        await _record_stage_decision(
            session,
            revision,
            stage=AutomationStage.TORRENT_SELECTION,
            action="CREATE_APPROVAL_REQUEST",
            outcome=outcome,
            media_item_id=media.id,
            reasons=reasons,
            evidence=evidence,
        )
        return None

    confirmed_review = await session.scalar(
        select(IdentityReview)
        .where(IdentityReview.media_id == media.id, IdentityReview.status == "CONFIRMED")
        .order_by(IdentityReview.created_at.desc(), IdentityReview.id.desc())
        .limit(1)
    )
    confirmed_identity: MetadataRecord | None = None
    if confirmed_review is not None:
        try:
            confirmed_identity = MetadataRecord.model_validate(
                confirmed_review.candidate_snapshot
            )
        except ValueError:
            confirmed_identity = None
    eligibility = evaluate_torrent_candidates(
        media,
        candidates,
        revision,
        search_job_id=job.id,
        site_id=run.site_id,
        max_size_bytes=settings.max_candidate_size_bytes,
        confirmed_identity=confirmed_identity,
    )
    if not eligibility.eligible or eligibility.selected_id is None:
        await _record_stage_decision(
            session,
            revision,
            stage=AutomationStage.TORRENT_SELECTION,
            action="CREATE_APPROVAL_REQUEST",
            outcome=DecisionOutcome.MANUAL_REQUIRED,
            media_item_id=media.id,
            torrent_candidate_id=eligibility.selected_id,
            reasons=eligibility.reason_codes,
            evidence=eligibility.evidence,
        )
        return None

    try:
        async with session.begin_nested():
            approval = await create_approval_request(
                session,
                eligibility.selected_id,
                ApprovalCreateRequest(),
                settings,
                actor=_automation_actor(revision),
            )
    except IntegrityError:
        existing_approval = await _get_active_approval_for_candidate(
            session, eligibility.selected_id
        )
        if existing_approval is None:
            raise
        approval = existing_approval
        await _record_stage_decision(
            session,
            revision,
            stage=AutomationStage.TORRENT_SELECTION,
            action="CREATE_APPROVAL_REQUEST",
            outcome=DecisionOutcome.NOOP,
            media_item_id=media.id,
            torrent_candidate_id=eligibility.selected_id,
            approval_request_id=approval.id,
            reasons=("ACTIVE_APPROVAL_EXISTS",),
            evidence=eligibility.evidence,
        )
        if approval.status == ApprovalStatus.PENDING:
            await maybe_enqueue_automatic_preflight(
                session,
                approval=approval,
                settings=settings,
            )
        return approval
    except AppError as exc:
        if exc.error_code not in {
            "APPROVAL_REQUEST_DUPLICATE",
            "APPROVAL_EXECUTION_IN_PROGRESS",
        }:
            raise
        existing_approval = await _get_active_approval_for_candidate(
            session, eligibility.selected_id
        )
        if existing_approval is None:
            raise
        approval = existing_approval
        await _record_stage_decision(
            session,
            revision,
            stage=AutomationStage.TORRENT_SELECTION,
            action="CREATE_APPROVAL_REQUEST",
            outcome=DecisionOutcome.NOOP,
            media_item_id=media.id,
            torrent_candidate_id=eligibility.selected_id,
            approval_request_id=approval.id,
            reasons=("ACTIVE_APPROVAL_EXISTS",),
            evidence=eligibility.evidence,
        )
        if approval.status == ApprovalStatus.PENDING:
            await maybe_enqueue_automatic_preflight(
                session,
                approval=approval,
                settings=settings,
            )
        return approval
    await _record_stage_decision(
        session,
        revision,
        stage=AutomationStage.TORRENT_SELECTION,
        action="CREATE_APPROVAL_REQUEST",
        outcome=DecisionOutcome.ACTION_CREATED,
        media_item_id=media.id,
        torrent_candidate_id=eligibility.selected_id,
        approval_request_id=approval.id,
        reasons=("TORRENT_CANDIDATE_ELIGIBLE",),
        evidence=eligibility.evidence,
    )
    await maybe_enqueue_automatic_preflight(
        session,
        approval=approval,
        settings=settings,
    )
    return approval


async def require_automatic_torrent_search_current(
    session: AsyncSession,
    *,
    job: Job,
    settings: Settings,
) -> None:
    policy_revision_id = job.payload.get("automation_policy_revision_id")
    decision_id = job.payload.get("automation_decision_id")
    if policy_revision_id is None and decision_id is None:
        return
    media_id = job.payload.get("media_id")
    search_run_id = job.payload.get("search_run_id")
    site_id = job.payload.get("site_id")
    if (
        not isinstance(policy_revision_id, str)
        or not isinstance(decision_id, str)
        or not isinstance(media_id, str)
        or not isinstance(search_run_id, str)
        or not isinstance(site_id, str)
    ):
        raise AppError(
            "AUTOMATION_TORRENT_SEARCH_BINDING_INVALID",
            "自动 PT 搜索任务绑定无效",
            status_code=409,
        )
    if not settings.enable_automation_engine:
        raise AppError(
            "AUTOMATION_ENGINE_DISABLED",
            "自动化引擎已关闭，禁止继续访问 PT 站点",
            status_code=409,
        )
    _, current = await get_current_policy(session)
    if current.id != policy_revision_id:
        raise AppError(
            "AUTOMATION_POLICY_REVISION_CHANGED",
            "自动 PT 搜索绑定的策略已不再是当前版本",
            status_code=409,
        )
    if (
        stage_mode(current, AutomationStage.TORRENT_SELECTION)
        != AutomationMode.AUTO_IF_ELIGIBLE
    ):
        raise AppError(
            "AUTOMATION_TORRENT_SELECTION_MODE_CHANGED",
            "当前策略不再允许自动 PT 搜索",
            status_code=409,
        )
    decision = await session.get(AutomationDecision, decision_id)
    if decision is None:
        raise AppError(
            "AUTOMATION_DECISION_NOT_FOUND",
            "自动 PT 搜索缺少决策绑定",
            status_code=409,
        )
    verify_automation_decision(decision)
    decision_search_run_id = decision.evidence_snapshot.get("search_run_id")
    decision_site_id = decision.evidence_snapshot.get("site_id")
    if (
        decision.policy_revision_id != current.id
        or decision.stage != AutomationStage.TORRENT_SELECTION
        or decision.action != "QUEUE_TORRENT_SEARCH"
        or decision.outcome != DecisionOutcome.ACTION_CREATED
        or decision.media_item_id != media_id
        or not isinstance(decision_search_run_id, str)
        or decision_search_run_id != search_run_id
        or not isinstance(decision_site_id, str)
        or decision_site_id != site_id
    ):
        raise AppError(
            "AUTOMATION_TORRENT_SEARCH_BINDING_INVALID",
            "自动 PT 搜索任务与决策记录不一致",
            status_code=409,
        )


async def maybe_enqueue_automatic_preflight(
    session: AsyncSession,
    *,
    approval: ApprovalRequest,
    settings: Settings,
) -> Job | None:
    _, revision = await get_current_policy(session, for_update=True)
    mode_outcome = _mode_outcome(
        revision,
        AutomationStage.APPROVAL,
        subject_created_at=approval.requested_at,
        engine_enabled=settings.enable_automation_engine,
    )
    evidence: dict[str, Any] = {
        "approval_request_id": approval.id,
        "approval_snapshot_hash": approval.snapshot_hash,
    }
    if mode_outcome is not None:
        outcome, reasons = mode_outcome
        await _record_stage_decision(
            session,
            revision,
            stage=AutomationStage.APPROVAL,
            action="QUEUE_PREFLIGHT",
            outcome=outcome,
            media_item_id=approval.media_item_id,
            approval_request_id=approval.id,
            reasons=reasons,
            evidence=evidence,
        )
        return None
    if approval.status != ApprovalStatus.PENDING:
        await _record_stage_decision(
            session,
            revision,
            stage=AutomationStage.APPROVAL,
            action="QUEUE_PREFLIGHT",
            outcome=DecisionOutcome.NOOP,
            media_item_id=approval.media_item_id,
            approval_request_id=approval.id,
            reasons=("APPROVAL_NOT_PENDING",),
            evidence=evidence,
        )
        return None
    snapshot = verify_snapshot(approval)
    if not effective_hnr_rule(snapshot.site_id, snapshot.hit_and_run).known:
        await _record_stage_decision(
            session,
            revision,
            stage=AutomationStage.APPROVAL,
            action="QUEUE_PREFLIGHT",
            outcome=DecisionOutcome.MANUAL_REQUIRED,
            media_item_id=approval.media_item_id,
            approval_request_id=approval.id,
            reasons=("HNR_UNKNOWN",),
            evidence=evidence,
        )
        return None
    preflight_worker_ready = await automation_preflight_is_ready(session, settings)
    evidence.update(
        {
            "preflight_capability_fingerprint": (
                automation_preflight_capability_fingerprint(settings)
            ),
            "preflight_worker_ready": preflight_worker_ready,
        }
    )
    if not preflight_worker_ready:
        await _record_stage_decision(
            session,
            revision,
            stage=AutomationStage.APPROVAL,
            action="QUEUE_PREFLIGHT",
            outcome=DecisionOutcome.BLOCKED,
            media_item_id=approval.media_item_id,
            approval_request_id=approval.id,
            reasons=("AUTOMATION_PREFLIGHT_WORKER_NOT_READY",),
            evidence=evidence,
        )
        return None

    job_type = f"AUTOMATION_PREFLIGHT:{approval.id}"
    existing = await session.scalar(
        select(Job)
        .where(
            Job.job_type == job_type,
            Job.status.in_((JobStatus.PENDING, JobStatus.RUNNING, JobStatus.RETRY_WAIT)),
        )
        .limit(1)
    )
    if existing is not None:
        await _record_stage_decision(
            session,
            revision,
            stage=AutomationStage.APPROVAL,
            action="QUEUE_PREFLIGHT",
            outcome=DecisionOutcome.NOOP,
            media_item_id=approval.media_item_id,
            approval_request_id=approval.id,
            reasons=("AUTOMATION_PREFLIGHT_ALREADY_ACTIVE",),
            evidence=evidence,
        )
        return existing
    job = Job(
        job_type=job_type,
        status=JobStatus.PENDING,
        payload={
            "approval_id": approval.id,
            "media_id": approval.media_item_id,
            "approval_snapshot_hash": approval.snapshot_hash,
            "automation_policy_revision_id": revision.id,
            "read_only": True,
        },
        max_attempts=settings.job_max_attempts,
    )
    try:
        async with session.begin_nested():
            session.add(job)
            await session.flush()
    except IntegrityError:
        existing_job: Job | None = await session.scalar(
            select(Job)
            .where(
                Job.job_type == job_type,
                Job.status.in_((JobStatus.PENDING, JobStatus.RUNNING, JobStatus.RETRY_WAIT)),
            )
            .limit(1)
        )
        if existing_job is None:
            raise
        await _record_stage_decision(
            session,
            revision,
            stage=AutomationStage.APPROVAL,
            action="QUEUE_PREFLIGHT",
            outcome=DecisionOutcome.NOOP,
            media_item_id=approval.media_item_id,
            approval_request_id=approval.id,
            reasons=("AUTOMATION_PREFLIGHT_ALREADY_ACTIVE",),
            evidence=evidence,
        )
        return existing_job
    decision = build_automation_decision(
        revision,
        stage=AutomationStage.APPROVAL,
        action="QUEUE_PREFLIGHT",
        outcome=DecisionOutcome.ACTION_CREATED,
        media_item_id=approval.media_item_id,
        approval_request_id=approval.id,
        reason_codes=("APPROVAL_ELIGIBLE_FOR_PREFLIGHT",),
        evidence={**evidence, "job_id": job.id},
    )
    stored, _ = await add_decision_once(session, decision)
    job.payload = {**job.payload, "automation_decision_id": stored.id}
    await session.flush()
    return job


async def maybe_finalize_automatic_approval(
    session: AsyncSession,
    *,
    approval: ApprovalRequest,
    settings: Settings,
    expected_policy_revision_id: str | None = None,
    expected_snapshot_hash: str | None = None,
    trigger_job_id: str | None = None,
) -> DownloadPlan | None:
    _, revision = await get_current_policy(session, for_update=True)
    evidence: dict[str, Any] = {
        "approval_request_id": approval.id,
        "approval_snapshot_hash": approval.snapshot_hash,
        "expected_policy_revision_id": expected_policy_revision_id,
        "trigger_job_id": trigger_job_id,
    }
    if (
        expected_policy_revision_id is not None
        and revision.id != expected_policy_revision_id
    ):
        await _record_stage_decision(
            session,
            revision,
            stage=AutomationStage.APPROVAL,
            action="APPROVE_REQUEST",
            outcome=DecisionOutcome.STALE,
            media_item_id=approval.media_item_id,
            approval_request_id=approval.id,
            reasons=("POLICY_REVISION_CHANGED",),
            evidence=evidence,
        )
        return None
    mode_outcome = _mode_outcome(
        revision,
        AutomationStage.APPROVAL,
        subject_created_at=approval.requested_at,
        engine_enabled=settings.enable_automation_engine,
    )
    if mode_outcome is not None:
        outcome, reasons = mode_outcome
        await _record_stage_decision(
            session,
            revision,
            stage=AutomationStage.APPROVAL,
            action="APPROVE_REQUEST",
            outcome=outcome,
            media_item_id=approval.media_item_id,
            approval_request_id=approval.id,
            reasons=reasons,
            evidence=evidence,
        )
        return None
    if expected_snapshot_hash is not None and approval.snapshot_hash != expected_snapshot_hash:
        await _record_stage_decision(
            session,
            revision,
            stage=AutomationStage.APPROVAL,
            action="APPROVE_REQUEST",
            outcome=DecisionOutcome.STALE,
            media_item_id=approval.media_item_id,
            approval_request_id=approval.id,
            reasons=("APPROVAL_SNAPSHOT_CHANGED",),
            evidence=evidence,
        )
        return None
    if approval.status != ApprovalStatus.PENDING:
        existing = await session.scalar(
            select(DownloadPlan).where(DownloadPlan.approval_id == approval.id).limit(1)
        )
        await _record_stage_decision(
            session,
            revision,
            stage=AutomationStage.APPROVAL,
            action="APPROVE_REQUEST",
            outcome=DecisionOutcome.NOOP,
            media_item_id=approval.media_item_id,
            approval_request_id=approval.id,
            reasons=("APPROVAL_NOT_PENDING",),
            evidence={**evidence, "approval_status": approval.status.value},
        )
        return existing

    snapshot = verify_snapshot(approval)
    if not effective_hnr_rule(snapshot.site_id, snapshot.hit_and_run).known:
        await _record_stage_decision(
            session,
            revision,
            stage=AutomationStage.APPROVAL,
            action="APPROVE_REQUEST",
            outcome=DecisionOutcome.MANUAL_REQUIRED,
            media_item_id=approval.media_item_id,
            approval_request_id=approval.id,
            reasons=("HNR_UNKNOWN",),
            evidence=evidence,
        )
        return None
    if approval.preflight_result is None or approval.preflight_checked_at is None:
        await _record_stage_decision(
            session,
            revision,
            stage=AutomationStage.APPROVAL,
            action="APPROVE_REQUEST",
            outcome=DecisionOutcome.MANUAL_REQUIRED,
            media_item_id=approval.media_item_id,
            approval_request_id=approval.id,
            reasons=("PREFLIGHT_REQUIRED",),
            evidence=evidence,
        )
        return None
    preflight = validate_preflight_result(approval.preflight_result)
    evidence.update(
        {
            "preflight_checked_at": preflight.checked_at.isoformat(),
            "preflight_policy_fingerprint": preflight.policy_fingerprint,
            "preflight_status": preflight.overall_status.value,
        }
    )
    if preflight.overall_status != PreflightStatus.PASS:
        await _record_stage_decision(
            session,
            revision,
            stage=AutomationStage.APPROVAL,
            action="APPROVE_REQUEST",
            outcome=DecisionOutcome.MANUAL_REQUIRED,
            media_item_id=approval.media_item_id,
            approval_request_id=approval.id,
            reasons=("AUTOMATION_PREFLIGHT_NOT_PASS",),
            evidence=evidence,
        )
        return None

    decision = build_automation_decision(
        revision,
        stage=AutomationStage.APPROVAL,
        action="APPROVE_REQUEST",
        outcome=DecisionOutcome.ACTION_CREATED,
        media_item_id=approval.media_item_id,
        approval_request_id=approval.id,
        reason_codes=("AUTOMATION_PREFLIGHT_PASS",),
        evidence=evidence,
    )
    stored, created = await add_decision_once(session, decision)
    if not created:
        existing_plan: DownloadPlan | None = await session.scalar(
            select(DownloadPlan).where(DownloadPlan.approval_id == approval.id).limit(1)
        )
        if existing_plan is None:
            raise AppError(
                "AUTOMATION_DECISION_ACTION_INCONSISTENT",
                "自动批准决策已存在但下载计划缺失",
                status_code=409,
            )
        return existing_plan
    _, plan = await approve_request_automatically(
        session,
        approval,
        settings,
        actor=_automation_actor(revision),
        policy_revision_id=revision.id,
        decision_id=stored.id,
    )
    await maybe_create_automatic_execution(
        session,
        approval=approval,
        settings=settings,
    )
    return plan


async def maybe_create_automatic_execution(
    session: AsyncSession,
    *,
    approval: ApprovalRequest,
    settings: Settings,
) -> DownloadExecution | None:
    _, revision = await get_current_policy(session, for_update=True)
    trigger_time = approval.decided_at or approval.requested_at
    mode_outcome = _mode_outcome(
        revision,
        AutomationStage.EXECUTION,
        subject_created_at=trigger_time,
        engine_enabled=settings.enable_automation_engine,
    )
    snapshot = verify_snapshot(approval)
    evidence = {
        "approval_request_id": approval.id,
        "approval_snapshot_hash": approval.snapshot_hash,
        "launch_mode": DownloadLaunchMode.ADD_PAUSED.value,
        "hnr_known": effective_hnr_rule(snapshot.site_id, snapshot.hit_and_run).known,
    }
    if mode_outcome is not None:
        outcome, reasons = mode_outcome
        await _record_stage_decision(
            session,
            revision,
            stage=AutomationStage.EXECUTION,
            action="CREATE_DOWNLOAD_EXECUTION",
            outcome=outcome,
            media_item_id=approval.media_item_id,
            approval_request_id=approval.id,
            reasons=reasons,
            evidence=evidence,
        )
        return None
    if not effective_hnr_rule(snapshot.site_id, snapshot.hit_and_run).known:
        await _record_stage_decision(
            session,
            revision,
            stage=AutomationStage.EXECUTION,
            action="CREATE_DOWNLOAD_EXECUTION",
            outcome=DecisionOutcome.MANUAL_REQUIRED,
            media_item_id=approval.media_item_id,
            approval_request_id=approval.id,
            reasons=("HNR_UNKNOWN",),
            evidence=evidence,
        )
        return None
    required_acks = {
        "acknowledges_seeding",
        "acknowledges_add_paused_only",
    }
    if hnr_acknowledgement_required(snapshot.site_id):
        required_acks.add("acknowledges_hnr")
    if not all(revision.acknowledgements.get(name) is True for name in required_acks):
        await _record_stage_decision(
            session,
            revision,
            stage=AutomationStage.EXECUTION,
            action="CREATE_DOWNLOAD_EXECUTION",
            outcome=DecisionOutcome.BLOCKED,
            media_item_id=approval.media_item_id,
            approval_request_id=approval.id,
            reasons=("AUTOMATION_EXECUTION_ACKNOWLEDGEMENTS_MISSING",),
            evidence=evidence,
        )
        return None
    existing = await session.scalar(
        select(DownloadExecution)
        .where(DownloadExecution.approval_id == approval.id)
        .limit(1)
    )
    if existing is not None:
        await verify_download_execution(session, existing)
        await _record_stage_decision(
            session,
            revision,
            stage=AutomationStage.EXECUTION,
            action="CREATE_DOWNLOAD_EXECUTION",
            outcome=DecisionOutcome.NOOP,
            media_item_id=approval.media_item_id,
            approval_request_id=approval.id,
            download_execution_id=existing.id,
            reasons=("DOWNLOAD_EXECUTION_ALREADY_EXISTS",),
            evidence=evidence,
        )
        return existing
    flags_ready = all(
        (
            settings.enable_download_execution_control_plane,
            settings.download_executor_enabled,
            settings.enable_avistaz_live_search,
            settings.qb_configured,
        )
    )
    executor_ready = flags_ready and await download_executor_is_ready(session, settings)
    evidence["download_executor_ready"] = executor_ready
    if not executor_ready:
        await _record_stage_decision(
            session,
            revision,
            stage=AutomationStage.EXECUTION,
            action="CREATE_DOWNLOAD_EXECUTION",
            outcome=DecisionOutcome.BLOCKED,
            media_item_id=approval.media_item_id,
            approval_request_id=approval.id,
            reasons=("DOWNLOAD_EXECUTOR_NOT_READY",),
            evidence=evidence,
        )
        return None

    execution_id = str(uuid.uuid4())
    decision = build_automation_decision(
        revision,
        stage=AutomationStage.EXECUTION,
        action="CREATE_DOWNLOAD_EXECUTION",
        outcome=DecisionOutcome.ACTION_CREATED,
        media_item_id=approval.media_item_id,
        approval_request_id=approval.id,
        download_execution_id=execution_id,
        reason_codes=("APPROVED_PLAN_ELIGIBLE",),
        evidence=evidence,
    )
    stored, created = await add_decision_once(session, decision)
    if not created:
        existing_execution: DownloadExecution | None = await session.scalar(
            select(DownloadExecution)
            .where(DownloadExecution.approval_id == approval.id)
            .limit(1)
        )
        if existing_execution is None:
            raise AppError(
                "AUTOMATION_DECISION_ACTION_INCONSISTENT",
                "自动执行决策已存在但执行记录缺失",
                status_code=409,
            )
        await verify_download_execution(session, existing_execution)
        return existing_execution
    actor = _automation_actor(revision)
    intent, nonce = await create_execution_intent(
        session,
        approval.id,
        ExecutionIntentCreateRequest(launch_mode=DownloadLaunchMode.ADD_PAUSED),
        settings,
        actor=actor,
        origin=Origin.AUTOMATION,
        automation_policy_revision_id=revision.id,
        automation_decision_id=stored.id,
    )
    try:
        execution, _ = await execute_approved_plan(
            session,
            approval.id,
            DownloadExecutionCreateRequest(intent_id=intent.id, nonce=nonce),
            f"auto_{secrets.token_urlsafe(32)}",
            settings,
            actor=actor,
            origin=Origin.AUTOMATION,
            automation_policy_revision_id=revision.id,
            automation_decision_id=stored.id,
            execution_id=execution_id,
        )
    finally:
        nonce = ""
    return execution


async def _record_stage_decision(
    session: AsyncSession,
    revision: AutomationPolicyRevision,
    *,
    stage: AutomationStage,
    action: str,
    outcome: DecisionOutcome,
    media_item_id: str,
    reasons: Iterable[str],
    evidence: dict[str, Any],
    metadata_match_id: str | None = None,
    torrent_candidate_id: str | None = None,
    approval_request_id: str | None = None,
    download_execution_id: str | None = None,
) -> AutomationDecision:
    decision = build_automation_decision(
        revision,
        stage=stage,
        action=action,
        outcome=outcome,
        media_item_id=media_item_id,
        metadata_match_id=metadata_match_id,
        torrent_candidate_id=torrent_candidate_id,
        approval_request_id=approval_request_id,
        download_execution_id=download_execution_id,
        reason_codes=reasons,
        evidence=evidence,
    )
    stored, _ = await add_decision_once(session, decision)
    return stored


async def _get_active_approval_for_candidate(
    session: AsyncSession, candidate_id: str
) -> ApprovalRequest | None:
    approval: ApprovalRequest | None = await session.scalar(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.torrent_candidate_id == candidate_id,
            ApprovalRequest.status.in_(
                (
                    ApprovalStatus.PENDING,
                    ApprovalStatus.APPROVED,
                    ApprovalStatus.EXECUTING,
                )
            ),
        )
        .order_by(ApprovalRequest.created_at.desc(), ApprovalRequest.id.desc())
        .limit(1)
    )
    return approval


def _mode_outcome(
    revision: AutomationPolicyRevision,
    stage: AutomationStage,
    *,
    subject_created_at: datetime,
    engine_enabled: bool,
) -> tuple[DecisionOutcome, tuple[str, ...]] | None:
    mode = stage_mode(revision, stage)
    if not subject_is_new_for_revision(subject_created_at, revision):
        return DecisionOutcome.STALE, ("SUBJECT_PREDATES_POLICY",)
    if mode == AutomationMode.DISABLED:
        return DecisionOutcome.DISABLED, ("POLICY_STAGE_DISABLED",)
    if mode == AutomationMode.MANUAL:
        return DecisionOutcome.MANUAL_REQUIRED, ("POLICY_REQUIRES_MANUAL_ACTION",)
    if not engine_enabled:
        return DecisionOutcome.BLOCKED, ("AUTOMATION_ENGINE_DISABLED",)
    return None


def _automation_actor(revision: AutomationPolicyRevision) -> str:
    return f"system:automation:r{revision.revision_no}"


def _metadata_title_exact(media: MediaItem, candidate: MetadataRecord) -> bool:
    source_titles = {
        normalized
        for normalized in (
            _normalized_title(media.title),
            _normalized_title(media.original_title),
        )
        if normalized
    }
    candidate_titles = {
        normalized
        for normalized in (
            _normalized_title(candidate.title),
            _normalized_title(candidate.chinese_title),
            _normalized_title(candidate.english_title),
            _normalized_title(candidate.original_title),
            *(_normalized_title(alias) for alias in candidate.aliases),
        )
        if normalized
    }
    return bool(source_titles.intersection(candidate_titles))


def _normalized_title(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
