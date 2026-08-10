from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.api.routes.executions as execution_routes
import app.services.executions as execution_services
from app.api.dependencies import (
    get_admin_principal,
    get_viewer_principal,
)
from app.core.auth import Principal
from app.core.config import Settings
from app.db.session import get_session
from app.errors import AppError
from app.main import app
from app.models.entities import (
    ApprovalRequest,
    DownloadExecution,
    DownloadExecutionEvent,
    DownloadPlan,
    ExecutionIntent,
    MediaItem,
    TorrentCandidateRecord,
    TorrentSearchRun,
)
from app.models.enums import (
    ApprovalStatus,
    AuthRole,
    DownloadExecutionStatus,
    DownloadLaunchMode,
    ExecutionIntentStatus,
    IdentityConfidence,
    MediaType,
    MetadataStatus,
    PreflightStatus,
    WorkflowStatus,
)
from app.schemas.approvals import (
    ApprovalCandidateSnapshot,
    ApprovalRevokeRequest,
    MediaDestinationPlan,
    PreflightCheck,
    PreflightResult,
    PromotionSnapshot,
)
from app.schemas.executions import (
    DownloadExecutionCreateRequest,
    DownloadExecutionReconcileRequest,
    ExecutionIntentCreateRequest,
)
from app.services.approvals import download_plan_hash, revoke_request, snapshot_hash
from app.services.executions import (
    create_execution_intent,
    execute_approved_plan,
    hash_secret,
    mark_outcome_unknown,
    persist_info_hash_before_submission,
    request_reconciliation,
)

INFO_HASH = "1234567890abcdef1234567890abcdef12345678"
IDEMPOTENCY_KEY = "execution-test-key-0000000001"


def execution_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "enable_download_execution_control_plane": True,
        "qb_base_url": "https://qb.internal.test",
        "qb_allowed_hosts": ("qb.internal.test",),
        "qb_target_save_path": "/downloads/movies/incoming",
        "qb_allowed_save_paths": ("/downloads/movies",),
        "qb_target_category": "movies",
        "qb_save_path_ref": "movies-root",
        "qb_plan_tags": ("unin-plan",),
        "qb_target_instance_ref": "qb-primary",
        "execution_intent_default_ttl_seconds": 300,
        "execution_intent_max_ttl_seconds": 900,
        "job_max_attempts": 3,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


async def seed_approved_plan(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    source_item_id: str = "execution-media",
) -> ApprovalRequest:
    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=1)
    tmdb_id = int(hashlib.sha256(source_item_id.encode()).hexdigest()[:7], 16)
    media = MediaItem(
        source="nextfind",
        source_item_id=source_item_id,
        media_type=MediaType.MOVIE,
        tmdb_id=tmdb_id,
        title="Execution Movie",
        year=2026,
        identity_confidence=IdentityConfidence.HIGH,
        metadata_status=MetadataStatus.RESOLVED,
        workflow_status=WorkflowStatus.TORRENT_REVIEW,
        discovered_at=now,
        updated_at=now,
    )
    async with session_factory() as session:
        session.add(media)
        await session.flush()
        search_run = TorrentSearchRun(
            media_id=media.id,
            site_id="avistaz",
            status=WorkflowStatus.TORRENT_REVIEW,
            candidate_count=1,
        )
        session.add(search_run)
        await session.flush()
        candidate = TorrentCandidateRecord(
            search_run_id=search_run.id,
            site_id="avistaz",
            torrent_id=f"torrent-{source_item_id}",
            candidate_snapshot={},
            match_score=0.99,
            match_reasons=["TMDB_ID_EXACT"],
            warnings=[],
        )
        session.add(candidate)
        await session.flush()

        immutable = ApprovalCandidateSnapshot(
            media_item_id=media.id,
            media_title=media.title,
            media_type=media.media_type,
            tmdb_id=media.tmdb_id,
            year=media.year,
            torrent_candidate_id=candidate.id,
            site_id="avistaz",
            torrent_id=candidate.torrent_id,
            torrent_ref=f"avistaz:details:{source_item_id}",
            release_title="Execution Movie 2026 1080p WEB-DL",
            size_bytes=1024,
            info_hash=INFO_HASH,
            promotion=PromotionSnapshot(download_factor=0, upload_factor=1),
            hit_and_run=False,
            match_score=0.99,
            match_reasons=["TMDB_ID_EXACT"],
            warnings=[],
            requested_at=now,
            expires_at=expires_at,
        )
        immutable_data: dict[str, object] = immutable.model_dump(mode="json")
        preflight = PreflightResult(
            overall_status=PreflightStatus.PASS,
            checks=[
                PreflightCheck(
                    code="QB_CONNECTION",
                    status=PreflightStatus.PASS,
                    message="ok",
                )
            ],
            checked_at=now,
            policy_fingerprint="a" * 64,
        )
        approval = ApprovalRequest(
            media_item_id=media.id,
            torrent_candidate_id=candidate.id,
            status=ApprovalStatus.APPROVED,
            candidate_snapshot=immutable_data,
            snapshot_hash=snapshot_hash(immutable_data),
            requested_by="approver",
            requested_at=now,
            expires_at=expires_at,
            decided_at=now,
            preflight_result=preflight.model_dump(mode="json"),
            preflight_checked_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(approval)
        await session.flush()
        destination = MediaDestinationPlan(
            mode="PLAN_ONLY_NO_FILE_OPERATION",
            media_item_id=media.id,
            media_type=media.media_type,
            tmdb_id=media.tmdb_id,
        )
        plan = DownloadPlan(
            approval_id=approval.id,
            approval_snapshot_hash=approval.snapshot_hash,
            preflight_policy_fingerprint=preflight.policy_fingerprint,
            plan_hash="",
            site_id="avistaz",
            torrent_ref=f"avistaz:details:{source_item_id}",
            expected_info_hash=INFO_HASH,
            release_title="Execution Movie 2026 1080p WEB-DL",
            save_path_ref="movies-root",
            category="movies",
            tags=["unin-plan"],
            estimated_size_bytes=1024,
            media_destination_plan=destination.model_dump(mode="json"),
            preflight_result=preflight.model_dump(mode="json"),
            warnings=[],
            created_at=now,
        )
        plan.plan_hash = download_plan_hash(plan)
        session.add(plan)
        await session.commit()
        return approval


@pytest.fixture
def execution_api_client_factory(
    session_factory: async_sessionmaker[AsyncSession],
):
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    principal = Principal(
        username="execution-admin",
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


@pytest.mark.asyncio
async def test_nonce_is_returned_once_but_only_sha256_is_persisted(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    approval = await seed_approved_plan(session_factory)
    settings = execution_settings()
    async with session_factory() as session:
        intent, nonce = await create_execution_intent(
            session,
            approval.id,
            ExecutionIntentCreateRequest(),
            settings,
            actor="execution-admin",
        )
        await session.commit()

        assert intent.nonce_sha256 == hashlib.sha256(nonce.encode()).hexdigest()
        assert intent.nonce_sha256 == hash_secret(nonce)
        assert nonce not in {
            str(value)
            for column in ExecutionIntent.__table__.columns
            if (value := getattr(intent, column.name)) is not None
        }
        with pytest.raises(AppError) as duplicate:
            await create_execution_intent(
                session,
                approval.id,
                ExecutionIntentCreateRequest(),
                settings,
                actor="execution-admin",
            )
        assert duplicate.value.error_code == "EXECUTION_INTENT_ALREADY_ACTIVE"


@pytest.mark.asyncio
async def test_execute_is_idempotent_and_does_not_consume_approval(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    approval = await seed_approved_plan(session_factory)
    settings = execution_settings()
    async with session_factory() as session:
        intent, nonce = await create_execution_intent(
            session,
            approval.id,
            ExecutionIntentCreateRequest(launch_mode=DownloadLaunchMode.ADD_PAUSED),
            settings,
            actor="execution-admin",
        )
        await session.commit()
        request = DownloadExecutionCreateRequest(intent_id=intent.id, nonce=nonce)
        execution, created = await execute_approved_plan(
            session,
            approval.id,
            request,
            IDEMPOTENCY_KEY,
            settings,
            actor="execution-admin",
        )
        await session.commit()

        assert created is True
        assert execution.status == DownloadExecutionStatus.PENDING
        assert execution.actual_info_hash is None
        assert execution.attempts == 0
        assert execution.max_attempts == 3
        stored_approval = await session.get(ApprovalRequest, approval.id)
        stored_intent = await session.get(ExecutionIntent, intent.id)
        assert stored_approval is not None
        assert stored_approval.status == ApprovalStatus.APPROVED
        assert stored_intent is not None
        assert stored_intent.status == ExecutionIntentStatus.CONSUMED

        replayed, replay_created = await execute_approved_plan(
            session,
            approval.id,
            request,
            IDEMPOTENCY_KEY,
            execution_settings(enable_download_execution_control_plane=False),
            actor="execution-admin",
        )
        assert replay_created is False
        assert replayed.id == execution.id

        wrong_nonce = DownloadExecutionCreateRequest(
            intent_id=intent.id,
            nonce="ei1_" + "x" * 43,
        )
        with pytest.raises(AppError) as reused_key:
            await execute_approved_plan(
                session,
                approval.id,
                wrong_nonce,
                IDEMPOTENCY_KEY,
                settings,
                actor="execution-admin",
            )
        assert reused_key.value.error_code == "IDEMPOTENCY_KEY_REUSED"

        with pytest.raises(AppError) as second_key:
            await execute_approved_plan(
                session,
                approval.id,
                request,
                "execution-test-key-0000000002",
                settings,
                actor="execution-admin",
            )
        assert second_key.value.error_code == "DOWNLOAD_EXECUTION_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_expired_intent_and_target_drift_fail_without_execution(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch,
) -> None:
    approval = await seed_approved_plan(session_factory)
    settings = execution_settings()
    async with session_factory() as session:
        intent, nonce = await create_execution_intent(
            session,
            approval.id,
            ExecutionIntentCreateRequest(expires_in_seconds=30),
            settings,
            actor="execution-admin",
        )
        await session.commit()
        monkeypatch.setattr(
            execution_services,
            "utc_now",
            lambda: intent.expires_at + timedelta(seconds=1),
        )
        with pytest.raises(AppError) as expired:
            await execute_approved_plan(
                session,
                approval.id,
                DownloadExecutionCreateRequest(intent_id=intent.id, nonce=nonce),
                IDEMPOTENCY_KEY,
                settings,
                actor="execution-admin",
            )
        assert expired.value.error_code == "EXECUTION_INTENT_EXPIRED"
        await session.commit()
        assert intent.status == ExecutionIntentStatus.EXPIRED
        assert (
            await session.scalar(
                select(DownloadExecution).where(DownloadExecution.approval_id == approval.id)
            )
            is None
        )

    second_approval = await seed_approved_plan(
        session_factory, source_item_id="execution-target-drift"
    )
    monkeypatch.setattr(execution_services, "utc_now", lambda: datetime.now(UTC))
    async with session_factory() as session:
        intent, nonce = await create_execution_intent(
            session,
            second_approval.id,
            ExecutionIntentCreateRequest(),
            settings,
            actor="execution-admin",
        )
        await session.commit()
        drifted = execution_settings(qb_target_save_path="/downloads/movies/changed")
        with pytest.raises(AppError) as mismatch:
            await execute_approved_plan(
                session,
                second_approval.id,
                DownloadExecutionCreateRequest(intent_id=intent.id, nonce=nonce),
                "execution-target-drift-00001",
                drifted,
                actor="execution-admin",
            )
        assert mismatch.value.error_code == "EXECUTION_INTENT_BINDING_MISMATCH"


@pytest.mark.asyncio
async def test_info_hash_is_persisted_before_unknown_outcome_and_reconciliation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    approval = await seed_approved_plan(
        session_factory, source_item_id="execution-reconcile"
    )
    settings = execution_settings()
    async with session_factory() as session:
        intent, nonce = await create_execution_intent(
            session,
            approval.id,
            ExecutionIntentCreateRequest(),
            settings,
            actor="execution-admin",
        )
        execution, _ = await execute_approved_plan(
            session,
            approval.id,
            DownloadExecutionCreateRequest(intent_id=intent.id, nonce=nonce),
            "execution-reconcile-key-00001",
            settings,
            actor="execution-admin",
        )
        execution.status = DownloadExecutionStatus.VALIDATING
        await session.flush()
        await persist_info_hash_before_submission(
            session, execution.id, INFO_HASH.upper(), actor="executor-test"
        )
        assert execution.status == DownloadExecutionStatus.SUBMITTING
        assert execution.actual_info_hash == INFO_HASH

        await mark_outcome_unknown(session, execution.id, actor="executor-test")
        assert execution.status == DownloadExecutionStatus.OUTCOME_UNKNOWN
        assert execution.next_retry_at is None
        reconciled = await request_reconciliation(
            session,
            execution.id,
            DownloadExecutionReconcileRequest(reason="manual read-only reconciliation"),
            actor="execution-admin",
        )
        assert reconciled.status == DownloadExecutionStatus.RECONCILIATION_PENDING
        repeated = await request_reconciliation(
            session,
            execution.id,
            DownloadExecutionReconcileRequest(reason="ignored replay"),
            actor="execution-admin",
        )
        assert repeated.status == DownloadExecutionStatus.RECONCILIATION_PENDING
        await session.commit()

        events = list(
            (
                await session.scalars(
                    select(DownloadExecutionEvent).where(
                        DownloadExecutionEvent.download_execution_id == execution.id
                    )
                )
            ).all()
        )
        assert [event.event_type for event in events].count("RECONCILIATION_REQUESTED") == 1
        assert any(event.event_type == "OUTCOME_UNKNOWN" for event in events)
        assert all(
            event.sanitized_details.get("external_request_performed") is not True
            for event in events
        )

        execution.actual_info_hash = "f" * 40
        with pytest.raises(ValueError, match="actual_info_hash"):
            await session.flush()


@pytest.mark.asyncio
async def test_revoked_queued_approval_cannot_cross_submission_gate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    approval = await seed_approved_plan(
        session_factory, source_item_id="execution-revoked-after-queue"
    )
    settings = execution_settings()
    async with session_factory() as session:
        intent, nonce = await create_execution_intent(
            session,
            approval.id,
            ExecutionIntentCreateRequest(),
            settings,
            actor="execution-admin",
        )
        execution, _ = await execute_approved_plan(
            session,
            approval.id,
            DownloadExecutionCreateRequest(intent_id=intent.id, nonce=nonce),
            "execution-revoked-key-000001",
            settings,
            actor="execution-admin",
        )
        stored_approval = await session.get(ApprovalRequest, approval.id)
        assert stored_approval is not None
        await revoke_request(
            session,
            stored_approval,
            ApprovalRevokeRequest(reason="cancel before executor submission"),
            actor="execution-admin",
        )
        execution.status = DownloadExecutionStatus.VALIDATING
        await session.flush()

        with pytest.raises(AppError) as invalidated:
            await persist_info_hash_before_submission(
                session,
                execution.id,
                INFO_HASH,
                actor="executor-test",
            )
        assert invalidated.value.error_code == "DOWNLOAD_EXECUTION_APPROVAL_INVALIDATED"
        await session.commit()

        assert stored_approval.status == ApprovalStatus.REVOKED
        assert execution.status == DownloadExecutionStatus.CANCELLED
        assert execution.actual_info_hash is None
        assert execution.next_retry_at is None


@pytest.mark.asyncio
async def test_execution_api_returns_nonce_only_on_intent_creation_and_replays_safely(
    session_factory: async_sessionmaker[AsyncSession],
    execution_api_client_factory,
    monkeypatch,
) -> None:
    approval = await seed_approved_plan(
        session_factory, source_item_id="execution-api"
    )
    monkeypatch.setattr(execution_routes, "get_settings", execution_settings)

    async with execution_api_client_factory() as client:
        intent_response = await client.post(
            f"/api/approval-requests/{approval.id}/execution-intents",
            json={"launch_mode": "ADD_PAUSED"},
        )
        assert intent_response.status_code == 201
        assert intent_response.headers["cache-control"] == "no-store"
        intent_payload = intent_response.json()
        nonce = intent_payload["nonce"]
        assert "nonce_sha256" not in intent_payload
        assert "qb.internal.test" not in intent_response.text

        body = {"intent_id": intent_payload["id"], "nonce": nonce}
        executed = await client.post(
            f"/api/approval-requests/{approval.id}/execute",
            json=body,
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
        )
        replayed = await client.post(
            f"/api/approval-requests/{approval.id}/execute",
            json=body,
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
        )
        by_approval = await client.get(
            f"/api/approval-requests/{approval.id}/download-execution"
        )
        by_id = await client.get(f"/api/download-executions/{executed.json()['id']}")
        listed = await client.get("/api/download-executions?status=PENDING")
        invalid_reconcile = await client.post(
            f"/api/download-executions/{executed.json()['id']}/reconcile",
            json={"reason": "not uncertain"},
        )

    assert executed.status_code == 201
    assert replayed.status_code == 200
    assert replayed.json()["id"] == executed.json()["id"]
    assert by_approval.status_code == 200
    assert by_id.status_code == 200
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [executed.json()["id"]]
    assert invalid_reconcile.status_code == 409
    assert invalid_reconcile.json()["error_code"] == "DOWNLOAD_EXECUTION_NOT_RECONCILABLE"
    for response in (executed, replayed, by_approval, by_id, listed):
        assert nonce not in response.text
        assert "idempotency" not in response.text.casefold()

    async with session_factory() as session:
        stored_approval = await session.get(ApprovalRequest, approval.id)
        stored_execution = await session.get(DownloadExecution, executed.json()["id"])
    assert stored_approval is not None
    assert stored_approval.status == ApprovalStatus.APPROVED
    assert stored_execution is not None
    assert stored_execution.idempotency_key_sha256 == hash_secret(IDEMPOTENCY_KEY)


@pytest.mark.asyncio
async def test_execution_control_plane_is_disabled_by_default(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    approval = await seed_approved_plan(
        session_factory, source_item_id="execution-disabled"
    )
    async with session_factory() as session:
        with pytest.raises(AppError) as disabled:
            await create_execution_intent(
                session,
                approval.id,
                ExecutionIntentCreateRequest(),
                execution_settings(enable_download_execution_control_plane=False),
                actor="execution-admin",
            )
    assert disabled.value.error_code == "DOWNLOAD_EXECUTION_CONTROL_PLANE_DISABLED"


def test_execution_uniqueness_constraints_cover_approval_intent_and_idempotency_key() -> None:
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in DownloadExecution.__table__.constraints
        if getattr(constraint, "unique", False)
        or constraint.__class__.__name__ == "UniqueConstraint"
    }
    unique_columns.update(
        tuple(column.name for column in index.columns)
        for index in DownloadExecution.__table__.indexes
        if index.unique
    )
    assert ("approval_id",) in unique_columns
    assert ("intent_id",) in unique_columns
    assert ("idempotency_key_sha256",) in unique_columns
    active_indexes = {
        index.name: index
        for index in ExecutionIntent.__table__.indexes
        if index.unique
    }
    assert "uq_execution_intent_active_approval" in active_indexes
