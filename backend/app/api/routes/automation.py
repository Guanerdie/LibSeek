from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.dependencies import AdminPrincipal, DbSession, SettingsDep, ViewerPrincipal
from app.core.security import sanitize_details
from app.models.entities import AutomationDecision, AutomationPolicyRevision
from app.models.enums import AutomationStage, DecisionOutcome
from app.schemas.automation import (
    AutomationDecisionResponse,
    AutomationPolicyCurrentResponse,
    AutomationPolicyRevisionCreateRequest,
    AutomationPolicyRevisionResponse,
)
from app.schemas.common import Page
from app.services.automation_policy import (
    get_automation_decision,
    get_current_policy,
    list_automation_decisions,
    list_policy_revisions,
    publish_policy_revision,
)

router = APIRouter(prefix="/automation", tags=["automation"])


def _revision_response(revision: AutomationPolicyRevision) -> AutomationPolicyRevisionResponse:
    rules = revision.eligibility_rules
    acknowledgements = revision.acknowledgements
    return AutomationPolicyRevisionResponse(
        id=revision.id,
        revision_no=revision.revision_no,
        identity_mode=revision.identity_mode,
        torrent_selection_mode=revision.torrent_selection_mode,
        approval_mode=revision.approval_mode,
        execution_mode=revision.execution_mode,
        identity_min_score=float(rules["identity_min_score"]),
        identity_min_margin=float(rules["identity_min_margin"]),
        torrent_min_score=float(rules["torrent_min_score"]),
        torrent_min_margin=float(rules["torrent_min_margin"]),
        torrent_min_seeders=int(rules["torrent_min_seeders"]),
        acknowledges_hnr=bool(acknowledgements["acknowledges_hnr"]),
        acknowledges_seeding=bool(acknowledgements["acknowledges_seeding"]),
        acknowledges_plan_only=bool(acknowledgements["acknowledges_plan_only"]),
        acknowledges_add_paused_only=bool(
            acknowledgements["acknowledges_add_paused_only"]
        ),
        policy_hash=revision.policy_hash,
        previous_policy_hash=revision.previous_policy_hash,
        effective_from=revision.effective_from,
        created_by=revision.created_by,
        created_at=revision.created_at,
    )


def _decision_response(decision: AutomationDecision) -> AutomationDecisionResponse:
    sanitized = sanitize_details(decision.evidence_snapshot)
    return AutomationDecisionResponse(
        id=decision.id,
        policy_revision_id=decision.policy_revision_id,
        stage=decision.stage,
        action=decision.action,
        outcome=decision.outcome,
        media_item_id=decision.media_item_id,
        metadata_match_id=decision.metadata_match_id,
        torrent_candidate_id=decision.torrent_candidate_id,
        approval_request_id=decision.approval_request_id,
        download_execution_id=decision.download_execution_id,
        reason_codes=[str(value) for value in sanitize_details(decision.reason_codes)],
        evidence_snapshot=sanitized,
        evidence_hash=decision.evidence_hash,
        actor=decision.actor,
        created_at=decision.created_at,
    )


@router.get("/policy", response_model=AutomationPolicyCurrentResponse)
async def get_automation_policy(
    session: DbSession,
    settings: SettingsDep,
    _principal: ViewerPrincipal,
) -> AutomationPolicyCurrentResponse:
    head, revision = await get_current_policy(session)
    await session.commit()
    return AutomationPolicyCurrentResponse(
        scope=head.scope,
        version=head.version,
        engine_enabled=bool(getattr(settings, "enable_automation_engine", False)),
        revision=_revision_response(revision),
    )


@router.get(
    "/policy-revisions", response_model=Page[AutomationPolicyRevisionResponse]
)
async def get_automation_policy_revisions(
    session: DbSession,
    _principal: ViewerPrincipal,
    page: int = Query(default=1, ge=1, le=100_000),
    page_size: int = Query(default=50, ge=1, le=100),
) -> Page[AutomationPolicyRevisionResponse]:
    revisions, total = await list_policy_revisions(session, page=page, page_size=page_size)
    await session.commit()
    return Page[AutomationPolicyRevisionResponse](
        items=[_revision_response(revision) for revision in revisions],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post(
    "/policy-revisions", response_model=AutomationPolicyCurrentResponse, status_code=201
)
async def create_automation_policy_revision(
    request: AutomationPolicyRevisionCreateRequest,
    session: DbSession,
    settings: SettingsDep,
    principal: AdminPrincipal,
) -> AutomationPolicyCurrentResponse:
    head, revision = await publish_policy_revision(
        session, request, actor=principal.username
    )
    await session.commit()
    return AutomationPolicyCurrentResponse(
        scope=head.scope,
        version=head.version,
        engine_enabled=bool(getattr(settings, "enable_automation_engine", False)),
        revision=_revision_response(revision),
    )


@router.get("/decisions", response_model=Page[AutomationDecisionResponse])
async def get_automation_decisions(
    session: DbSession,
    _principal: ViewerPrincipal,
    page: int = Query(default=1, ge=1, le=100_000),
    page_size: int = Query(default=50, ge=1, le=100),
    stage: AutomationStage | None = None,
    outcome: DecisionOutcome | None = None,
    media_item_id: str | None = Query(default=None, min_length=1, max_length=36),
) -> Page[AutomationDecisionResponse]:
    decisions, total = await list_automation_decisions(
        session,
        page=page,
        page_size=page_size,
        stage=stage,
        outcome=outcome,
        media_item_id=media_item_id,
    )
    return Page[AutomationDecisionResponse](
        items=[_decision_response(decision) for decision in decisions],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/decisions/{decision_id}", response_model=AutomationDecisionResponse)
async def get_automation_decision_by_id(
    decision_id: str,
    session: DbSession,
    _principal: ViewerPrincipal,
) -> AutomationDecisionResponse:
    return _decision_response(await get_automation_decision(session, decision_id))
