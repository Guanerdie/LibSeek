from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import get_admin_principal, get_viewer_principal
from app.core.auth import Principal
from app.core.security import sanitize_details
from app.db.session import get_session
from app.errors import AppError
from app.main import app
from app.models.entities import AutomationDecision, MediaItem
from app.models.enums import (
    AuthRole,
    AutomationMode,
    AutomationStage,
    DecisionOutcome,
    IdentityConfidence,
    MediaType,
    MetadataStatus,
    WorkflowStatus,
)
from app.schemas.automation import AutomationPolicyRevisionCreateRequest
from app.services.automation_policy import (
    DEFAULT_ACKNOWLEDGEMENTS,
    DEFAULT_ELIGIBILITY_RULES,
    calculate_decision_dedupe_key,
    calculate_decision_evidence_hash,
    get_current_policy,
    list_policy_revisions,
    publish_policy_revision,
    verify_automation_decision,
)


@pytest.fixture
def automation_api_client_factory(
    session_factory: async_sessionmaker[AsyncSession],
):
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    principal = Principal(
        username="automation-admin",
        role=AuthRole.ADMIN,
        issued_at=0,
        expires_at=2**31,
        csrf_digest="0" * 64,
    )

    async def override_principal() -> Principal:
        return principal

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_viewer_principal] = override_principal
    app.dependency_overrides[get_admin_principal] = override_principal

    def create() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )

    yield create
    app.dependency_overrides.clear()


def policy_request(
    *,
    base_revision_no: int = 1,
    approval_mode: AutomationMode = AutomationMode.MANUAL,
    execution_mode: AutomationMode = AutomationMode.MANUAL,
    acknowledged: bool = False,
) -> AutomationPolicyRevisionCreateRequest:
    return AutomationPolicyRevisionCreateRequest(
        base_revision_no=base_revision_no,
        identity_mode=AutomationMode.MANUAL,
        torrent_selection_mode=AutomationMode.MANUAL,
        approval_mode=approval_mode,
        execution_mode=execution_mode,
        acknowledges_hnr=acknowledged,
        acknowledges_seeding=acknowledged,
        acknowledges_plan_only=acknowledged,
        acknowledges_add_paused_only=acknowledged,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("created_by", "attacker-controlled"),
        ("launch_mode", "IMMEDIATE"),
        ("immediate_start", True),
    ),
)
async def test_policy_revision_api_rejects_server_controlled_extra_fields(
    automation_api_client_factory,
    field: str,
    value: object,
) -> None:
    payload = policy_request(acknowledged=True).model_dump(mode="json")
    payload[field] = value

    async with automation_api_client_factory() as client:
        response = await client.post("/api/automation/policy-revisions", json=payload)

    assert response.status_code == 422
    body = response.json()
    assert body["error_code"] == "API_VALIDATION_ERROR"
    assert any(
        error == {"location": f"body.{field}", "type": "extra_forbidden"}
        for error in body["details"]["fields"]
    )


def media_item(media_id: str = "media-automation") -> MediaItem:
    now = datetime.now(UTC)
    return MediaItem(
        id=media_id,
        source="nextfind",
        source_item_id=media_id,
        media_type=MediaType.MOVIE,
        title="Automation Test",
        identity_confidence=IdentityConfidence.NEEDS_CONFIRMATION,
        metadata_status=MetadataStatus.NEEDS_CONFIRMATION,
        workflow_status=WorkflowStatus.DISCOVERED,
        discovered_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_default_policy_is_manual_hashed_and_versioned(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        head, revision = await get_current_policy(session)
        await session.commit()

    assert head.scope == "global"
    assert head.version == revision.revision_no == 1
    assert revision.identity_mode == AutomationMode.MANUAL
    assert revision.torrent_selection_mode == AutomationMode.MANUAL
    assert revision.approval_mode == AutomationMode.MANUAL
    assert revision.execution_mode == AutomationMode.MANUAL
    assert revision.eligibility_rules == DEFAULT_ELIGIBILITY_RULES
    assert revision.acknowledgements == DEFAULT_ACKNOWLEDGEMENTS
    assert len(revision.policy_hash) == 64
    assert revision.previous_policy_hash is None


@pytest.mark.asyncio
async def test_publish_uses_hash_chain_cas_and_required_acknowledgements(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await get_current_policy(session)
        await session.commit()

        with pytest.raises(AppError) as missing_ack:
            await publish_policy_revision(
                session,
                policy_request(approval_mode=AutomationMode.AUTO_IF_ELIGIBLE),
                actor="admin",
            )
        assert missing_ack.value.error_code == "AUTOMATION_ACKNOWLEDGEMENTS_REQUIRED"
        await session.rollback()

        head, revision = await publish_policy_revision(
            session,
            policy_request(
                execution_mode=AutomationMode.AUTO_IF_ELIGIBLE,
                acknowledged=True,
            ),
            actor="admin",
        )
        await session.commit()
        assert head.version == revision.revision_no == 2
        assert revision.previous_policy_hash is not None
        assert revision.execution_mode == AutomationMode.AUTO_IF_ELIGIBLE

        with pytest.raises(AppError) as conflict:
            await publish_policy_revision(session, policy_request(), actor="other-admin")
        assert conflict.value.error_code == "AUTOMATION_POLICY_REVISION_CONFLICT"
        assert conflict.value.details == {"current_revision_no": 2}

        revisions, total = await list_policy_revisions(session, page=1, page_size=10)
        assert total == 2
        assert [item.revision_no for item in revisions] == [2, 1]


@pytest.mark.asyncio
async def test_policy_revision_and_head_guards_reject_mutation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        head, revision = await get_current_policy(session)
        await session.commit()
        revision.created_by = "tampered"
        with pytest.raises(ValueError, match="immutable"):
            await session.flush()
        await session.rollback()

    async with session_factory() as session:
        head, _ = await get_current_policy(session)
        head.version += 2
        head.current_revision_id = "invalid-revision"
        with pytest.raises(ValueError, match="advance by one"):
            await session.flush()


@pytest.mark.asyncio
async def test_policy_hash_tampering_is_detected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        _, revision = await get_current_policy(session)
        await session.commit()
        await session.execute(
            update(type(revision))
            .where(type(revision).id == revision.id)
            .values(policy_hash="0" * 64)
        )
        await session.commit()

    async with session_factory() as session:
        with pytest.raises(AppError) as tampered:
            await get_current_policy(session)
        assert tampered.value.error_code == "AUTOMATION_POLICY_TAMPERED"


@pytest.mark.asyncio
async def test_concurrent_default_initialization_recovers_existing_head() -> None:
    from app.services.automation_policy import _new_default_policy

    head, revision = _new_default_policy()

    class NestedTransaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class ConcurrentSession:
        def __init__(self) -> None:
            self.scalar_results = iter((None, head))

        async def scalar(self, _statement):
            return next(self.scalar_results)

        def begin_nested(self):
            return NestedTransaction()

        def add_all(self, _values) -> None:
            return None

        async def flush(self) -> None:
            raise IntegrityError("insert", {}, Exception("duplicate"))

        async def get(self, _model, _identifier):
            return revision

    recovered_head, recovered_revision = await get_current_policy(ConcurrentSession())  # type: ignore[arg-type]
    assert recovered_head is head
    assert recovered_revision is revision


@pytest.mark.asyncio
async def test_policy_and_decision_api_are_stable_and_redacted(
    session_factory: async_sessionmaker[AsyncSession], automation_api_client_factory
) -> None:
    async with automation_api_client_factory() as client:
        current = await client.get("/api/automation/policy")
        assert current.status_code == 200
        assert current.json()["engine_enabled"] is False
        assert current.json()["revision"]["identity_min_score"] == 0.5

        created = await client.post(
            "/api/automation/policy-revisions",
            json=policy_request(acknowledged=True).model_dump(mode="json"),
        )
        assert created.status_code == 201
        revision = created.json()["revision"]
        assert revision["revision_no"] == 2
        assert revision["previous_policy_hash"] == current.json()["revision"]["policy_hash"]

        revisions = await client.get(
            "/api/automation/policy-revisions?page=1&page_size=1"
        )
        assert revisions.json()["total"] == 2
        assert revisions.json()["items"][0]["revision_no"] == 2

    async with session_factory() as session:
        session.add(media_item())
        head, revision_record = await get_current_policy(session)
        assert head.version == 2
        evidence_snapshot = {
            "download_url": "https://secret.invalid/download?passkey=value",
            "nested": {"text": "https://secret.invalid/visible"},
        }
        evidence_hash = calculate_decision_evidence_hash(evidence_snapshot)
        dedupe_key = calculate_decision_dedupe_key(
            policy_revision_id=revision_record.id,
            stage=AutomationStage.IDENTITY,
            action="CONFIRM_IDENTITY",
            outcome=DecisionOutcome.MANUAL_REQUIRED,
            media_item_id="media-automation",
            metadata_match_id=None,
            torrent_candidate_id=None,
            approval_request_id=None,
            download_execution_id=None,
            reason_codes=["AMBIGUOUS", "https://secret.invalid/path"],
            evidence_hash=evidence_hash,
        )
        decision = AutomationDecision(
            policy_revision_id=revision_record.id,
            stage=AutomationStage.IDENTITY,
            action="CONFIRM_IDENTITY",
            outcome=DecisionOutcome.MANUAL_REQUIRED,
            media_item_id="media-automation",
            reason_codes=["AMBIGUOUS", "https://secret.invalid/path"],
            evidence_snapshot=evidence_snapshot,
            evidence_hash=evidence_hash,
            dedupe_key=dedupe_key,
        )
        session.add(decision)
        await session.commit()
        decision_id = decision.id

    async with automation_api_client_factory() as client:
        listed = await client.get(
            "/api/automation/decisions?stage=IDENTITY&outcome=MANUAL_REQUIRED"
        )
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        item = listed.json()["items"][0]
        assert "dedupe_key" not in item
        assert item["evidence_snapshot"] == {
            "download_url": "[REDACTED]",
            "nested": {"text": "[URL_REDACTED]"},
        }
        assert item["reason_codes"] == sanitize_details(
            ["AMBIGUOUS", "https://secret.invalid/path"]
        )

        detail = await client.get(f"/api/automation/decisions/{decision_id}")
        assert detail.status_code == 200
        assert detail.json() == item
        missing = await client.get("/api/automation/decisions/missing")
        assert missing.status_code == 404
        assert missing.json()["error_code"] == "AUTOMATION_DECISION_NOT_FOUND"

    decision.dedupe_key = "0" * 64
    with pytest.raises(AppError) as invalid_binding:
        verify_automation_decision(decision)
    assert invalid_binding.value.error_code == "AUTOMATION_DECISION_BINDING_INVALID"
    decision.dedupe_key = dedupe_key
    decision.evidence_snapshot = {"changed": True}
    with pytest.raises(AppError) as tampered_evidence:
        verify_automation_decision(decision)
    assert tampered_evidence.value.error_code == "AUTOMATION_DECISION_TAMPERED"
