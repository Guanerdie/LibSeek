from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.services.executions as execution_services
from app.api.dependencies import (
    get_admin_principal,
    get_viewer_principal,
)
from app.core.auth import Principal
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.errors import AppError
from app.main import app
from app.models.entities import (
    ApprovalEvent,
    ApprovalRequest,
    DownloadExecution,
    DownloadExecutionEvent,
    DownloadJob,
    DownloadJobEvent,
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
    DownloadJobStatus,
    DownloadLaunchMode,
    ExecutionIntentStatus,
    HnrStatus,
    IdentityConfidence,
    MediaType,
    MetadataStatus,
    Origin,
    PreflightStatus,
    WorkflowStatus,
)
from app.repositories.jobs import find_download_job_by_execution_id
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
from app.schemas.qbittorrent import QbTorrent
from app.services.approvals import (
    build_qb_plan_tags,
    download_plan_hash,
    revoke_request,
    snapshot_hash,
)
from app.services.executions import (
    create_execution_intent,
    execute_approved_plan,
    finalize_download_submission,
    hash_secret,
    list_download_executions,
    mark_outcome_unknown,
    persist_info_hash_before_submission,
    request_reconciliation,
    update_download_job_observation,
)
from app.services.jobs import get_download_job_for_execution
from app.services.preflight import preflight_policy_fingerprint
from app.services.torrent_validation import ValidatedTorrent

INFO_HASH = "1234567890abcdef1234567890abcdef12345678"
V2_INFO_HASH = "abcdef0123456789" * 4
IDEMPOTENCY_KEY = "execution-test-key-0000000001"


def validated_torrent(
    *,
    info_hash_v1: str | None = INFO_HASH,
    info_hash_v2: str | None = None,
) -> ValidatedTorrent:
    return ValidatedTorrent(
        name="Execution Movie 2026 1080p WEB-DL",
        total_size_bytes=1024,
        file_count=1,
        info_hash_v1=info_hash_v1,
        info_hash_v2=info_hash_v2,
        private=True,
        meta_version=2 if info_hash_v2 is not None else 1,
    )


def qb_observation(
    *,
    info_hash: str = INFO_HASH,
    info_hash_v1: str | None = INFO_HASH,
    info_hash_v2: str | None = None,
    state: str = "pausedDL",
    progress: float = 0.25,
    category: str = "movies",
    save_path: str = "/downloads/movies/incoming",
    size: int = 1024,
    downloaded: int = 256,
) -> QbTorrent:
    return QbTorrent(
        hash=info_hash,
        infohash_v1=info_hash_v1,
        infohash_v2=info_hash_v2,
        name="Execution Movie 2026 1080p WEB-DL",
        size=size,
        progress=progress,
        ratio=0.5,
        state=state,
        downloaded=downloaded,
        uploaded=128,
        dlspeed=2048,
        upspeed=512,
        category=category,
        save_path=save_path,
    )


def claim_for_validation(execution: DownloadExecution) -> str:
    lease_token = str(uuid.uuid4())
    execution.status = DownloadExecutionStatus.VALIDATING
    execution.attempts += 1
    execution.locked_at = datetime.now(UTC)
    execution.locked_by = "executor-test"
    execution.lease_token = lease_token
    return lease_token


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
    expected_info_hash: str | None = INFO_HASH,
) -> ApprovalRequest:
    now = datetime.now(UTC)
    policy_settings = execution_settings()
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
            info_hash=expected_info_hash,
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
            policy_fingerprint=preflight_policy_fingerprint(policy_settings),
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
            expected_info_hash=expected_info_hash,
            release_title="Execution Movie 2026 1080p WEB-DL",
            save_path_ref="movies-root",
            category="movies",
            tags=list(build_qb_plan_tags(policy_settings.qb_plan_tags, media.title)),
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


async def create_staged_execution(
    session: AsyncSession,
    approval: ApprovalRequest,
    *,
    idempotency_key: str,
    metadata: ValidatedTorrent | None = None,
) -> tuple[DownloadExecution, str]:
    settings = execution_settings()
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
        idempotency_key,
        settings,
        actor="execution-admin",
    )
    lease_token = claim_for_validation(execution)
    await session.flush()
    staged = await persist_info_hash_before_submission(
        session,
        execution.id,
        metadata or validated_torrent(),
        actor="executor-test",
        lease_token=lease_token,
    )
    assert staged.status == DownloadExecutionStatus.SUBMITTING
    return execution, lease_token


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


@pytest.fixture
def execution_viewer_api_client_factory(
    session_factory: async_sessionmaker[AsyncSession],
):
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    principal = Principal(
        username="execution-viewer",
        role=AuthRole.VIEWER,
        issued_at=0,
        expires_at=2**31,
        csrf_digest="0" * 64,
    )

    async def override_principal() -> Principal:
        return principal

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_viewer_principal] = override_principal

    def create() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )

    yield create
    app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", (Origin.MANUAL, Origin.AUTOMATION))
@pytest.mark.parametrize("target_change", ("base_url", "instance_ref"))
async def test_execution_intent_rejects_preflight_target_drift_without_audit_writes(
    session_factory: async_sessionmaker[AsyncSession],
    origin: Origin,
    target_change: str,
) -> None:
    approval = await seed_approved_plan(
        session_factory,
        source_item_id=f"intent-drift-{origin.value}-{target_change}",
    )
    if target_change == "base_url":
        drifted = execution_settings(
            qb_base_url="https://qb-secondary.internal.test",
            qb_allowed_hosts=("qb-secondary.internal.test",),
        )
    else:
        drifted = execution_settings(qb_target_instance_ref="qb-secondary")

    async with session_factory() as session:
        events_before = list(
            (
                await session.scalars(
                    select(ApprovalEvent).where(
                        ApprovalEvent.approval_request_id == approval.id
                    )
                )
            ).all()
        )
        with pytest.raises(AppError) as caught:
            await create_execution_intent(
                session,
                approval.id,
                ExecutionIntentCreateRequest(),
                drifted,
                actor="execution-admin",
                origin=origin,
                automation_policy_revision_id=(
                    "policy-revision-test" if origin == Origin.AUTOMATION else None
                ),
                automation_decision_id=(
                    "automation-decision-test" if origin == Origin.AUTOMATION else None
                ),
            )

        assert caught.value.error_code == "PREFLIGHT_CONFIG_CHANGED"
        assert caught.value.status_code == 409
        assert "qb-secondary.internal.test" not in caught.value.message
        assert (
            await session.scalar(
                select(ExecutionIntent)
                .where(ExecutionIntent.approval_id == approval.id)
                .limit(1)
            )
            is None
        )
        events_after = list(
            (
                await session.scalars(
                    select(ApprovalEvent).where(
                        ApprovalEvent.approval_request_id == approval.id
                    )
                )
            ).all()
        )
        assert events_after == events_before


@pytest.mark.asyncio
async def test_execution_intent_accepts_unchanged_target_with_opaque_fingerprints(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    approval = await seed_approved_plan(
        session_factory,
        source_item_id="intent-unchanged-target",
    )
    settings = execution_settings(
        qb_username="private-user",
        qb_password="private-password",
    )
    async with session_factory() as session:
        intent, _ = await create_execution_intent(
            session,
            approval.id,
            ExecutionIntentCreateRequest(),
            settings,
            actor="execution-admin",
        )
        plan = await session.scalar(
            select(DownloadPlan).where(DownloadPlan.approval_id == approval.id).limit(1)
        )

        assert plan is not None
        assert plan.preflight_policy_fingerprint == preflight_policy_fingerprint(settings)
        public_fingerprints = json.dumps(
            {
                "preflight": plan.preflight_policy_fingerprint,
                "target": intent.qb_target_fingerprint,
            }
        )
        assert all(
            secret not in public_fingerprints
            for secret in (
                "qb.internal.test",
                "private-user",
                "private-password",
            )
        )


@pytest.mark.asyncio
async def test_execution_rejects_policy_drift_after_intent_without_consuming_it_or_audit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    approval = await seed_approved_plan(
        session_factory,
        source_item_id="execution-policy-drift-after-intent",
    )
    settings = execution_settings()
    drifted = execution_settings(max_candidate_size_bytes=512)

    async with session_factory() as session:
        intent, nonce = await create_execution_intent(
            session,
            approval.id,
            ExecutionIntentCreateRequest(),
            settings,
            actor="execution-admin",
        )
        events_before = list(
            (
                await session.scalars(
                    select(ApprovalEvent).where(
                        ApprovalEvent.approval_request_id == approval.id
                    )
                )
            ).all()
        )

        with pytest.raises(AppError) as caught:
            await execute_approved_plan(
                session,
                approval.id,
                DownloadExecutionCreateRequest(intent_id=intent.id, nonce=nonce),
                "execution-policy-drift-idempotency-key",
                drifted,
                actor="execution-admin",
            )

        assert caught.value.error_code == "PREFLIGHT_CONFIG_CHANGED"
        assert caught.value.status_code == 409
        assert (
            await session.scalar(
                select(DownloadExecution)
                .where(DownloadExecution.approval_id == approval.id)
                .limit(1)
            )
            is None
        )
        await session.refresh(intent)
        assert intent.status == ExecutionIntentStatus.ACTIVE
        events_after = list(
            (
                await session.scalars(
                    select(ApprovalEvent).where(
                        ApprovalEvent.approval_request_id == approval.id
                    )
                )
            ).all()
        )
        assert events_after == events_before


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
        assert mismatch.value.error_code == "PREFLIGHT_CONFIG_CHANGED"


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
        lease_token = claim_for_validation(execution)
        await session.flush()
        await persist_info_hash_before_submission(
            session,
            execution.id,
            validated_torrent(),
            actor="executor-test",
            lease_token=lease_token,
        )
        assert execution.status == DownloadExecutionStatus.SUBMITTING
        assert execution.actual_info_hash == INFO_HASH

        await mark_outcome_unknown(
            session,
            execution.id,
            actor="executor-test",
            lease_token=lease_token,
        )
        assert execution.status == DownloadExecutionStatus.OUTCOME_UNKNOWN
        assert execution.next_retry_at is None
        assert execution.lease_token is None
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
        lease_token = claim_for_validation(execution)
        await session.flush()

        terminal = await persist_info_hash_before_submission(
            session,
            execution.id,
            validated_torrent(),
            actor="executor-test",
            lease_token=lease_token,
        )
        assert terminal.status == DownloadExecutionStatus.CANCELLED
        await session.commit()

        assert stored_approval.status == ApprovalStatus.REVOKED
        assert execution.status == DownloadExecutionStatus.CANCELLED
        assert execution.actual_info_hash is None
        assert execution.next_retry_at is None


@pytest.mark.asyncio
async def test_expired_lease_cannot_cross_submission_or_finalize_gate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    approval = await seed_approved_plan(
        session_factory, source_item_id="execution-expired-lease"
    )
    settings = execution_settings(
        download_execution_lease_seconds=30,
        download_execution_lease_renew_interval_seconds=10,
    )
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
            "execution-expired-lease-key-01",
            settings,
            actor="execution-admin",
        )
        lease_token = claim_for_validation(execution)
        execution.locked_at = datetime.now(UTC) - timedelta(seconds=31)
        await session.flush()

        with pytest.raises(AppError) as expired_validation:
            await persist_info_hash_before_submission(
                session,
                execution.id,
                validated_torrent(),
                actor="executor-test",
                lease_token=lease_token,
                lease_seconds=settings.download_execution_lease_seconds,
            )
        assert expired_validation.value.error_code == "DOWNLOAD_EXECUTION_LEASE_LOST"
        assert execution.status == DownloadExecutionStatus.VALIDATING

        execution.locked_at = datetime.now(UTC)
        await persist_info_hash_before_submission(
            session,
            execution.id,
            validated_torrent(),
            actor="executor-test",
            lease_token=lease_token,
            lease_seconds=settings.download_execution_lease_seconds,
        )
        execution.locked_at = datetime.now(UTC) - timedelta(seconds=31)
        await session.flush()
        with pytest.raises(AppError) as expired_finalize:
            await finalize_download_submission(
                session,
                execution.id,
                qb_observation(),
                DownloadExecutionStatus.SUBMITTED,
                settings,
                actor="executor-test",
                lease_token=lease_token,
            )
        assert expired_finalize.value.error_code == "DOWNLOAD_EXECUTION_LEASE_LOST"
        assert execution.status == DownloadExecutionStatus.SUBMITTING


@pytest.mark.asyncio
async def test_finalize_submission_consumes_approval_and_creates_sanitized_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    approval = await seed_approved_plan(
        session_factory, source_item_id="execution-finalize"
    )
    async with session_factory() as session:
        execution, lease_token = await create_staged_execution(
            session,
            approval,
            idempotency_key="execution-finalize-key-00001",
        )
        observed = qb_observation()
        job = await finalize_download_submission(
            session,
            execution.id,
            observed,
            DownloadExecutionStatus.SUBMITTED,
            execution_settings(),
            actor="executor-test",
            lease_token=lease_token,
        )
        assert job.status == DownloadJobStatus.PAUSED
        assert job.save_path_ref == "movies-root"
        assert job.info_hash_v1 == INFO_HASH
        assert job.progress == 0.25
        assert job.download_speed_bps == 2048
        assert job.hnr_status == HnrStatus.UNKNOWN
        assert execution.status == DownloadExecutionStatus.SUBMITTED
        assert execution.verified_at is not None
        assert execution.lease_token is None

        replayed = await finalize_download_submission(
            session,
            execution.id,
            observed,
            DownloadExecutionStatus.SUBMITTED,
            execution_settings(),
            actor="executor-test",
            lease_token="stale-token",
        )
        assert replayed.id == job.id
        await session.commit()

        stored_approval = await session.get(ApprovalRequest, approval.id)
        assert stored_approval is not None
        assert stored_approval.status == ApprovalStatus.CONSUMED
        assert (
            await session.scalar(
                select(DownloadJob).where(DownloadJob.execution_id == execution.id)
            )
        ).id == job.id
        execution_events = list(
            (
                await session.scalars(
                    select(DownloadExecutionEvent).where(
                        DownloadExecutionEvent.download_execution_id == execution.id
                    )
                )
            ).all()
        )
        job_events = list(
            (
                await session.scalars(
                    select(DownloadJobEvent).where(DownloadJobEvent.download_job_id == job.id)
                )
            ).all()
        )
        serialized_details = json.dumps(
            [event.sanitized_details for event in (*execution_events, *job_events)]
        )
        assert "/downloads/movies/incoming" not in serialized_details
        assert [event.event_type for event in job_events] == ["CREATED"]


@pytest.mark.asyncio
async def test_finalize_accepts_v2_truncated_alias_and_already_present(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    approval = await seed_approved_plan(
        session_factory,
        source_item_id="execution-v2-finalize",
        expected_info_hash=V2_INFO_HASH[:40],
    )
    metadata = validated_torrent(info_hash_v1=None, info_hash_v2=V2_INFO_HASH)
    async with session_factory() as session:
        execution, lease_token = await create_staged_execution(
            session,
            approval,
            idempotency_key="execution-v2-finalize-key-01",
            metadata=metadata,
        )
        job = await finalize_download_submission(
            session,
            execution.id,
            qb_observation(
                info_hash=V2_INFO_HASH[:40],
                info_hash_v1=None,
                info_hash_v2=V2_INFO_HASH,
            ),
            DownloadExecutionStatus.ALREADY_PRESENT,
            execution_settings(),
            actor="executor-test",
            lease_token=lease_token,
        )
        await session.commit()
        assert execution.status == DownloadExecutionStatus.ALREADY_PRESENT
        assert execution.actual_info_hash == V2_INFO_HASH[:40]
        assert job.info_hash_v1 is None
        assert job.info_hash_v2 == V2_INFO_HASH


@pytest.mark.asyncio
async def test_finalize_fences_lease_and_rejects_observation_drift(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    approval = await seed_approved_plan(
        session_factory, source_item_id="execution-finalize-drift"
    )
    async with session_factory() as session:
        execution, lease_token = await create_staged_execution(
            session,
            approval,
            idempotency_key="execution-finalize-drift-01",
        )
        with pytest.raises(AppError) as stale:
            await finalize_download_submission(
                session,
                execution.id,
                qb_observation(),
                DownloadExecutionStatus.SUBMITTED,
                execution_settings(),
                actor="executor-test",
                lease_token=str(uuid.uuid4()),
            )
        assert stale.value.error_code == "DOWNLOAD_EXECUTION_LEASE_LOST"

        observations = (
            (qb_observation(category="wrong"), "QB_SUBMISSION_TARGET_MISMATCH"),
            (
                qb_observation(save_path="/downloads/other"),
                "QB_SUBMISSION_TARGET_MISMATCH",
            ),
            (
                qb_observation(
                    info_hash="f" * 40,
                    info_hash_v1="f" * 40,
                ),
                "QB_SUBMISSION_INFO_HASH_MISMATCH",
            ),
        )
        for observed, error_code in observations:
            with pytest.raises(AppError) as mismatch:
                await finalize_download_submission(
                    session,
                    execution.id,
                    observed,
                    DownloadExecutionStatus.SUBMITTED,
                    execution_settings(),
                    actor="executor-test",
                    lease_token=lease_token,
                )
            assert mismatch.value.error_code == error_code
        assert execution.status == DownloadExecutionStatus.SUBMITTING
        assert (
            await session.scalar(
                select(DownloadJob).where(DownloadJob.execution_id == execution.id)
            )
            is None
        )


@pytest.mark.asyncio
async def test_monitor_updates_metrics_maps_states_and_keeps_hnr_unknown(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    approval = await seed_approved_plan(
        session_factory, source_item_id="execution-monitor"
    )
    async with session_factory() as session:
        execution, lease_token = await create_staged_execution(
            session,
            approval,
            idempotency_key="execution-monitor-key-00001",
        )
        job = await finalize_download_submission(
            session,
            execution.id,
            qb_observation(),
            DownloadExecutionStatus.SUBMITTED,
            execution_settings(),
            actor="executor-test",
            lease_token=lease_token,
        )
        first_seen = datetime.now(UTC) + timedelta(seconds=1)
        job.hnr_status = HnrStatus.AT_RISK
        seeding = await update_download_job_observation(
            session,
            job.id,
            qb_observation(
                state="uploading",
                progress=1,
                downloaded=1024,
            ),
            expected_save_path="/downloads/movies/incoming",
            actor="monitor-test",
            observed_at=first_seen,
        )
        assert seeding.status == DownloadJobStatus.SEEDING
        assert seeding.progress == 1
        assert seeding.downloaded_bytes == 1024
        assert seeding.completed_at is not None
        assert seeding.hnr_status == HnrStatus.AT_RISK

        missing = await update_download_job_observation(
            session,
            job.id,
            None,
            expected_save_path="/downloads/movies/incoming",
            actor="monitor-test",
            observed_at=first_seen + timedelta(seconds=15),
        )
        assert missing.status == DownloadJobStatus.MISSING
        assert missing.last_seen_at == first_seen
        assert missing.hnr_status == HnrStatus.AT_RISK
        await session.commit()

        status_events = list(
            (
                await session.scalars(
                    select(DownloadJobEvent)
                    .where(DownloadJobEvent.download_job_id == job.id)
                    .order_by(DownloadJobEvent.created_at, DownloadJobEvent.id)
                )
            ).all()
        )
        assert [event.to_status for event in status_events] == [
            DownloadJobStatus.PAUSED.value,
            DownloadJobStatus.SEEDING.value,
            DownloadJobStatus.MISSING.value,
        ]


@pytest.mark.asyncio
async def test_monitor_marks_save_path_drift_without_persisting_real_paths(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    approval = await seed_approved_plan(
        session_factory, source_item_id="execution-monitor-save-path-drift"
    )
    approved_path = "/downloads/movies/incoming"
    observed_path = "/private/unapproved/location"
    async with session_factory() as session:
        execution, lease_token = await create_staged_execution(
            session,
            approval,
            idempotency_key="execution-monitor-path-key-01",
        )
        job = await finalize_download_submission(
            session,
            execution.id,
            qb_observation(),
            DownloadExecutionStatus.SUBMITTED,
            execution_settings(),
            actor="executor-test",
            lease_token=lease_token,
        )
        drifted = await update_download_job_observation(
            session,
            job.id,
            qb_observation(save_path=observed_path),
            expected_save_path=approved_path,
            actor="monitor-test",
        )
        await session.commit()

        assert drifted.status == DownloadJobStatus.ERROR
        assert drifted.error_code == "QB_TORRENT_BINDING_DRIFT"
        event = await session.scalar(
            select(DownloadJobEvent)
            .where(
                DownloadJobEvent.download_job_id == job.id,
                DownloadJobEvent.event_type == "STATUS_CHANGED",
                DownloadJobEvent.to_status == DownloadJobStatus.ERROR.value,
            )
            .order_by(DownloadJobEvent.created_at.desc(), DownloadJobEvent.id.desc())
        )
        assert event is not None
        persisted = json.dumps(
            {
                "error_message": drifted.error_message,
                "details": event.sanitized_details,
            },
            ensure_ascii=False,
        )
        assert approved_path not in persisted
        assert observed_path not in persisted


@pytest.mark.asyncio
async def test_download_job_repository_finds_unique_execution_binding(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    approval = await seed_approved_plan(
        session_factory, source_item_id="execution-job-repository"
    )
    async with session_factory() as session:
        execution, lease_token = await create_staged_execution(
            session,
            approval,
            idempotency_key="execution-job-repository-key-01",
        )
        job = await finalize_download_submission(
            session,
            execution.id,
            qb_observation(),
            DownloadExecutionStatus.SUBMITTED,
            execution_settings(),
            actor="executor-test",
            lease_token=lease_token,
        )
        await session.commit()

    async with session_factory() as session:
        found = await find_download_job_by_execution_id(session, execution.id)
        missing = await find_download_job_by_execution_id(session, "missing-execution")

    assert found is not None
    assert found.id == job.id
    assert missing is None


@pytest.mark.asyncio
async def test_download_job_service_distinguishes_missing_execution_and_pending_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    approval = await seed_approved_plan(
        session_factory, source_item_id="execution-job-service"
    )
    async with session_factory() as session:
        execution, lease_token = await create_staged_execution(
            session,
            approval,
            idempotency_key="execution-job-service-key-0001",
        )
        await session.commit()

    async with session_factory() as session:
        with pytest.raises(AppError) as pending_job:
            await get_download_job_for_execution(session, execution.id)
        with pytest.raises(AppError) as missing_execution:
            await get_download_job_for_execution(session, "missing-execution")

    assert pending_job.value.status_code == 404
    assert pending_job.value.error_code == "DOWNLOAD_JOB_NOT_CREATED"
    assert missing_execution.value.status_code == 404
    assert missing_execution.value.error_code == "DOWNLOAD_EXECUTION_NOT_FOUND"

    async with session_factory() as session:
        job = await finalize_download_submission(
            session,
            execution.id,
            qb_observation(),
            DownloadExecutionStatus.SUBMITTED,
            execution_settings(),
            actor="executor-test",
            lease_token=lease_token,
        )
        resolved = await get_download_job_for_execution(session, execution.id)
        await session.commit()

    assert resolved.id == job.id


@pytest.mark.asyncio
async def test_viewer_can_get_download_job_for_execution_with_stable_not_found_errors(
    session_factory: async_sessionmaker[AsyncSession],
    execution_viewer_api_client_factory,
) -> None:
    approval = await seed_approved_plan(
        session_factory, source_item_id="execution-job-viewer-api"
    )
    async with session_factory() as session:
        execution, lease_token = await create_staged_execution(
            session,
            approval,
            idempotency_key="execution-job-viewer-api-key-01",
        )
        await session.commit()

    async with execution_viewer_api_client_factory() as client:
        pending_job = await client.get(
            f"/api/download-executions/{execution.id}/download-job"
        )
        missing_execution = await client.get(
            "/api/download-executions/missing-execution/download-job"
        )

    assert pending_job.status_code == 404
    assert pending_job.json()["error_code"] == "DOWNLOAD_JOB_NOT_CREATED"
    assert missing_execution.status_code == 404
    assert missing_execution.json()["error_code"] == "DOWNLOAD_EXECUTION_NOT_FOUND"

    async with session_factory() as session:
        job = await finalize_download_submission(
            session,
            execution.id,
            qb_observation(),
            DownloadExecutionStatus.SUBMITTED,
            execution_settings(),
            actor="executor-test",
            lease_token=lease_token,
        )
        await session.commit()

    async with execution_viewer_api_client_factory() as client:
        response = await client.get(
            f"/api/download-executions/{execution.id}/download-job"
        )

    assert response.status_code == 200
    assert response.json()["id"] == job.id
    assert response.json()["execution_id"] == execution.id
    assert "lease_token" not in response.text
    assert "locked_by" not in response.text


@pytest.mark.asyncio
async def test_download_job_read_apis_are_paginated_nested_and_redacted(
    session_factory: async_sessionmaker[AsyncSession],
    execution_api_client_factory,
) -> None:
    approval = await seed_approved_plan(
        session_factory, source_item_id="execution-job-api"
    )
    async with session_factory() as session:
        execution, lease_token = await create_staged_execution(
            session,
            approval,
            idempotency_key="execution-job-api-key-0001",
        )
        job = await finalize_download_submission(
            session,
            execution.id,
            qb_observation(),
            DownloadExecutionStatus.SUBMITTED,
            execution_settings(),
            actor="executor-test",
            lease_token=lease_token,
        )
        session.add(
            DownloadJobEvent(
                download_job_id=job.id,
                event_type="SANITIZE_TEST",
                from_status=job.status.value,
                to_status=job.status.value,
                actor="monitor-test",
                sanitized_details={"token": "must-not-leak", "note": "visible"},
            )
        )
        await session.commit()

    async with execution_api_client_factory() as client:
        listed = await client.get(
            "/api/download-jobs?page=1&page_size=10&status=PAUSED"
        )
        detail = await client.get(f"/api/download-jobs/{job.id}")
        timeline = await client.get(f"/api/download-jobs/{job.id}/timeline")
        summary = await client.get(f"/api/download-jobs/{job.id}/summary")

    assert listed.status_code == detail.status_code == 200
    assert timeline.status_code == summary.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == job.id
    assert detail.json()["save_path_ref"] == "movies-root"
    timeline_payload = timeline.json()
    assert timeline_payload["job_id"] == job.id
    sanitize_item = next(
        item for item in timeline_payload["items"] if item["event_type"] == "SANITIZE_TEST"
    )
    assert sanitize_item["sanitized_details"] == {
        "token": "[REDACTED]",
        "note": "visible",
    }
    summary_payload = summary.json()
    assert set(summary_payload) == {
        "job",
        "media",
        "approval",
        "execution",
        "warnings",
    }
    assert summary_payload["media"]["title"] == "Execution Movie"
    assert summary_payload["approval"]["status"] == "CONSUMED"
    assert summary_payload["execution"]["status"] == "SUBMITTED"
    assert "HNR_STATUS_UNKNOWN" in summary_payload["warnings"]
    for response in (listed, detail, timeline, summary):
        assert "locked_by" not in response.text
        assert "lease_token" not in response.text
        assert "/downloads/movies/incoming" not in response.text
        assert "must-not-leak" not in response.text


@pytest.mark.asyncio
async def test_execution_api_returns_nonce_only_on_intent_creation_and_replays_safely(
    session_factory: async_sessionmaker[AsyncSession],
    execution_api_client_factory,
    monkeypatch,
) -> None:
    approval = await seed_approved_plan(
        session_factory, source_item_id="execution-api"
    )
    app.dependency_overrides[get_settings] = lambda: execution_settings()

    async with execution_api_client_factory() as client:
        intent_response = await client.post(
            f"/api/approval-requests/{approval.id}/execution-intents",
            json={"launch_mode": "ADD_PAUSED"},
        )
        assert intent_response.status_code == 201, intent_response.text
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
    assert listed.json()["page"] == 1
    assert listed.json()["page_size"] == 50
    assert listed.json()["total"] == 1
    assert [item["id"] for item in listed.json()["items"]] == [executed.json()["id"]]
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

    async with session_factory() as session:
        stored_execution = await session.get(DownloadExecution, executed.json()["id"])
        assert stored_execution is not None
        stored_execution.error_code = "QB_NETWORK_ERROR"
        stored_execution.error_message = (
            "request to https://qb.internal.test/api/v2/torrents/info failed "
            "for /data or D:/data"
        )
        await session.commit()
    async with execution_api_client_factory() as client:
        redacted = await client.get(f"/api/download-executions/{executed.json()['id']}")
    assert redacted.status_code == 200
    assert "qb.internal.test" not in redacted.text
    assert "/data" not in redacted.text
    assert "D:/data" not in redacted.text
    assert redacted.json()["error_message"] == "下载执行异常，请根据错误代码查看审计记录"


@pytest.mark.asyncio
async def test_execution_list_uses_stable_page_and_id_tiebreaker(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch,
) -> None:
    approvals = [
        await seed_approved_plan(
            session_factory,
            source_item_id=f"execution-page-{index}",
        )
        for index in range(2)
    ]
    fixed_now = datetime.now(UTC)
    monkeypatch.setattr(execution_services, "utc_now", lambda: fixed_now)
    execution_ids: list[str] = []
    async with session_factory() as session:
        for index, approval in enumerate(approvals):
            intent, nonce = await create_execution_intent(
                session,
                approval.id,
                ExecutionIntentCreateRequest(),
                execution_settings(),
                actor="execution-admin",
            )
            execution, _ = await execute_approved_plan(
                session,
                approval.id,
                DownloadExecutionCreateRequest(intent_id=intent.id, nonce=nonce),
                f"execution-page-key-0000{index}",
                execution_settings(),
                actor="execution-admin",
            )
            execution_ids.append(execution.id)
        await session.commit()

        first, total = await list_download_executions(session, page=1, page_size=1)
        second, repeated_total = await list_download_executions(
            session, page=2, page_size=1
        )
    assert total == repeated_total == 2
    assert [first[0].id, second[0].id] == sorted(execution_ids, reverse=True)


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


@pytest.mark.asyncio
async def test_database_checks_reject_invalid_execution_state_across_sessions(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    approval = await seed_approved_plan(
        session_factory, source_item_id="execution-database-checks"
    )
    async with session_factory() as session:
        intent, nonce = await create_execution_intent(
            session,
            approval.id,
            ExecutionIntentCreateRequest(),
            execution_settings(),
            actor="execution-admin",
        )
        execution, _ = await execute_approved_plan(
            session,
            approval.id,
            DownloadExecutionCreateRequest(intent_id=intent.id, nonce=nonce),
            "execution-database-check-key-01",
            execution_settings(),
            actor="execution-admin",
        )
        await session.commit()
        execution_id = execution.id

    async with session_factory() as corrupting_session:
        with pytest.raises(IntegrityError):
            await corrupting_session.execute(
                text(
                    "UPDATE download_executions SET actual_info_hash = :mismatch "
                    "WHERE id = :execution_id"
                ),
                {"execution_id": execution_id, "mismatch": "f" * 40},
            )
            await corrupting_session.commit()
        await corrupting_session.rollback()

        with pytest.raises(IntegrityError):
            await corrupting_session.execute(
                text(
                    "UPDATE download_executions "
                    "SET status = 'VALIDATING', locked_at = NULL, locked_by = NULL, "
                    "lease_token = NULL WHERE id = :execution_id"
                ),
                {"execution_id": execution_id},
            )
            await corrupting_session.commit()
        await corrupting_session.rollback()

    async with session_factory() as verification_session:
        persisted = await verification_session.get(DownloadExecution, execution_id)
        assert persisted is not None
        assert persisted.status == DownloadExecutionStatus.PENDING
        assert persisted.locked_at is None
        assert persisted.locked_by is None
        assert persisted.lease_token is None

        lease_token = claim_for_validation(persisted)
        await verification_session.flush()
        await persist_info_hash_before_submission(
            verification_session,
            persisted.id,
            validated_torrent(),
            actor="executor-test",
            lease_token=lease_token,
        )
        await verification_session.commit()

    async with session_factory() as corrupting_session:
        with pytest.raises(IntegrityError):
            await corrupting_session.execute(
                text(
                    "UPDATE download_executions "
                    "SET actual_info_hash = :invalid_hash, "
                    "actual_info_hash_v1 = :invalid_hash WHERE id = :execution_id"
                ),
                {"execution_id": execution_id, "invalid_hash": "z" * 40},
            )
            await corrupting_session.commit()
        await corrupting_session.rollback()

    async with session_factory() as final_session:
        persisted = await final_session.get(DownloadExecution, execution_id)
        assert persisted is not None
        assert persisted.actual_info_hash == INFO_HASH
        assert persisted.actual_info_hash_v1 == INFO_HASH

    v2_approval = await seed_approved_plan(
        session_factory,
        source_item_id="execution-v2-database-checks",
        expected_info_hash=V2_INFO_HASH[:40],
    )
    async with session_factory() as session:
        v2_execution, _ = await create_staged_execution(
            session,
            v2_approval,
            idempotency_key="execution-v2-database-check-key",
            metadata=validated_torrent(info_hash_v1=None, info_hash_v2=V2_INFO_HASH),
        )
        await session.commit()
        v2_execution_id = v2_execution.id

    async with session_factory() as corrupting_session:
        with pytest.raises(IntegrityError):
            await corrupting_session.execute(
                text(
                    "UPDATE download_executions SET actual_info_hash = :mismatch "
                    "WHERE id = :execution_id"
                ),
                {"execution_id": v2_execution_id, "mismatch": "f" * 40},
            )
            await corrupting_session.commit()
        await corrupting_session.rollback()

    async with session_factory() as final_session:
        persisted = await final_session.get(DownloadExecution, v2_execution_id)
        assert persisted is not None
        assert persisted.actual_info_hash == V2_INFO_HASH[:40]
        assert persisted.actual_info_hash_v1 is None
        assert persisted.actual_info_hash_v2 == V2_INFO_HASH


def test_orm_metadata_exposes_named_download_state_constraints() -> None:
    execution_checks = {
        constraint.name
        for constraint in DownloadExecution.__table__.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    job_checks = {
        constraint.name
        for constraint in DownloadJob.__table__.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    assert {
        "ck_download_executions_attempts",
        "ck_download_executions_lease_fields",
        "ck_download_executions_lease_status",
        "ck_download_executions_validation_bundle",
        "ck_download_executions_submission_metadata",
        "ck_download_executions_reconciliation_fields",
        "ck_download_executions_actual_info_hash_binding",
        "ck_download_executions_reconciliation_reason",
    } <= execution_checks
    assert {
        "ck_download_jobs_info_hash_required",
        "ck_download_jobs_content_bounds",
        "ck_download_jobs_progress",
        "ck_download_jobs_transfer_metrics",
    } <= job_checks
