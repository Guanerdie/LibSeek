from __future__ import annotations

import builtins
import inspect
import os
import shutil
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import (
    CheckConstraint,
    func,
    select,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy import (
    event as sqlalchemy_event,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.schemas.media_imports as media_import_schemas
import app.services.media_imports as media_import_services
from app.api.dependencies import (
    get_admin_principal,
    get_operator_principal,
    get_viewer_principal,
)
from app.core.auth import CSRF_HEADER_NAME, Principal
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.errors import AppError
from app.main import app
from app.models.entities import (
    DownloadExecution,
    DownloadJob,
    MediaImportEvent,
    MediaImportPlan,
    MediaImportPreflight,
    MediaImportRequest,
    MediaItem,
)
from app.models.enums import (
    AuthRole,
    DownloadExecutionStatus,
    DownloadJobStatus,
    HnrStatus,
    MediaImportOperation,
    MediaImportStatus,
    PreflightStatus,
)
from app.schemas.media_imports import (
    MediaImportApproveRequest,
    MediaImportCreateRequest,
    MediaImportDecisionRequest,
    MediaImportInspectedSourceFile,
    MediaImportInspectedTargetFile,
    MediaImportInspectionSnapshot,
    MediaImportManifestFile,
    MediaImportPreflightResult,
    MediaImportSourceManifest,
    MediaImportTargetEntry,
    MediaImportTargetMapping,
)
from app.services.executions import (
    finalize_download_submission,
    update_download_job_observation,
)
from app.services.media_imports import (
    approve_media_import_request,
    create_media_import_request,
    media_import_request_response,
    record_media_import_preflight,
    reject_media_import_request,
    revoke_media_import_request,
)
from tests.test_execution_control_plane import (
    INFO_HASH,
    create_staged_execution,
    execution_settings,
    qb_observation,
    seed_approved_plan,
)


def import_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "enable_media_import_control_plane": True,
        "media_import_target_root_refs": ("library-movies", "library-tv"),
        "media_import_preflight_max_age_seconds": 300,
    }
    values.update(overrides)
    return execution_settings(**values)


AUTH_USERNAME = "media-import-admin"
AUTH_PASSWORD = "media-import-test-password"
AUTH_SIGNING_KEY = "media-import-test-signing-key-at-least-32-characters"


def authenticated_import_settings(role: AuthRole) -> Settings:
    return import_settings(
        auth_local_username=AUTH_USERNAME,
        auth_local_password=AUTH_PASSWORD,
        auth_session_signing_key=AUTH_SIGNING_KEY,
        auth_local_role=role,
    )


async def login_for_media_import(client: httpx.AsyncClient) -> str:
    csrf = await client.get("/api/auth/csrf")
    assert csrf.status_code == 200
    bootstrap_token = csrf.json()["csrf_token"]
    response = await client.post(
        "/api/auth/login",
        json={"username": AUTH_USERNAME, "password": AUTH_PASSWORD},
        headers={CSRF_HEADER_NAME: bootstrap_token},
    )
    assert response.status_code == 200
    return str(response.json()["csrf_token"])


async def seed_ready_job(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    source_item_id: str,
) -> DownloadJob:
    approval = await seed_approved_plan(
        session_factory,
        source_item_id=source_item_id,
    )
    async with session_factory() as session:
        execution, lease_token = await create_staged_execution(
            session,
            approval,
            idempotency_key=f"media-import-{source_item_id}-0001",
        )
        job = await finalize_download_submission(
            session,
            execution.id,
            qb_observation(
                state="pausedUP",
                progress=1,
                downloaded=1024,
            ),
            DownloadExecutionStatus.SUBMITTED,
            execution_settings(),
            actor="executor-test",
            lease_token=lease_token,
        )
        job = await update_download_job_observation(
            session,
            job.id,
            qb_observation(
                state="uploading",
                progress=1,
                downloaded=1024,
            ),
            expected_save_path="/downloads/movies/incoming",
            actor="monitor-test",
        )
        await session.commit()
        assert job.status == DownloadJobStatus.SEEDING
        assert job.progress == 1
        return job


def proposal(job_id: str, *, operation: MediaImportOperation = MediaImportOperation.HARDLINK):
    return MediaImportCreateRequest(
        download_job_id=job_id,
        proposed_operation=operation,
        source_manifest=MediaImportSourceManifest(
            source_root_ref="movies-root",
            files=[
                MediaImportManifestFile(
                    relative_path="Execution.Movie.2026.mkv",
                    size_bytes=1024,
                )
            ],
        ),
        target_mapping=MediaImportTargetMapping(
            target_root_ref="library-movies",
            files=[
                MediaImportTargetEntry(
                    source_relative_path="Execution.Movie.2026.mkv",
                    target_relative_path=(
                        "Execution Movie (2026) [tmdbid=1]/"
                        "Execution Movie (2026) [tmdbid=1].mkv"
                    ),
                )
            ],
            source_retention=True,
            overwrite=False,
        ),
    )


def trusted_inspection(
    job_id: str,
    *,
    inspected_at: datetime | None = None,
    target_exists: bool = False,
    source_exists: bool = True,
    source_regular: bool = True,
    source_symlink: bool = False,
    source_complete: bool = True,
    same_filesystem: bool | None = True,
    available_bytes: int | None = None,
) -> MediaImportInspectionSnapshot:
    return MediaImportInspectionSnapshot(
        download_job_id=job_id,
        source_root_ref="movies-root",
        target_root_ref="library-movies",
        info_hash_v1=INFO_HASH,
        source_files=[
            MediaImportInspectedSourceFile(
                relative_path="Execution.Movie.2026.mkv",
                size_bytes=1024,
                exists=source_exists,
                is_regular_file=source_regular,
                is_symlink=source_symlink,
                complete=source_complete,
            )
        ],
        target_files=[
            MediaImportInspectedTargetFile(
                relative_path=(
                    "Execution Movie (2026) [tmdbid=1]/"
                    "Execution Movie (2026) [tmdbid=1].mkv"
                ),
                exists=target_exists,
            )
        ],
        same_filesystem=same_filesystem,
        available_bytes=available_bytes,
        inspected_at=inspected_at or datetime.now(UTC),
    )


def approval_confirmation() -> MediaImportApproveRequest:
    return MediaImportApproveRequest(
        acknowledges_plan_only=True,
        acknowledges_source_retention=True,
        acknowledges_no_overwrite=True,
        acknowledges_hnr=True,
    )


def test_paths_reject_unsafe_colliding_and_oversized_aggregate_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for path in (
        "/absolute/file.mkv",
        "C:/drive/file.mkv",
        "\\\\server\\share\\file.mkv",
        "Season 01/../escape.mkv",
        "Season 01\\file.mkv",
        "delete\u007fmarker.mkv",
        "spoof\u202emkv.txt",
        "zero\u200bwidth.mkv",
    ):
        with pytest.raises(ValidationError):
            MediaImportManifestFile(relative_path=path, size_bytes=1)

    with pytest.raises(ValidationError, match="case-colliding"):
        MediaImportSourceManifest(
            source_root_ref="movies-root",
            files=[
                MediaImportManifestFile(relative_path="Movie.mkv", size_bytes=1),
                MediaImportManifestFile(relative_path="movie.MKV", size_bytes=1),
            ],
        )

    # Regression: sorted-neighbour checks miss a -> a/b when a.b sorts between them.
    with pytest.raises(ValidationError, match="prefix collisions"):
        MediaImportSourceManifest(
            source_root_ref="movies-root",
            files=[
                MediaImportManifestFile(relative_path="a", size_bytes=1),
                MediaImportManifestFile(relative_path="a.b", size_bytes=1),
                MediaImportManifestFile(relative_path="a/b", size_bytes=1),
            ],
        )

    def reject_expensive_collision_check(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("collision checks must run after aggregate limits")

    with monkeypatch.context() as bounded:
        bounded.setattr(
            media_import_schemas,
            "MAX_MEDIA_IMPORT_AGGREGATE_PATH_BYTES",
            12,
        )
        bounded.setattr(
            media_import_schemas,
            "_validate_unique_paths",
            reject_expensive_collision_check,
        )
        with pytest.raises(ValidationError, match="aggregate path data"):
            MediaImportSourceManifest(
                source_root_ref="movies-root",
                files=[
                    MediaImportManifestFile(relative_path="long-name-1", size_bytes=1),
                    MediaImportManifestFile(relative_path="long-name-2", size_bytes=1),
                ],
            )
        with pytest.raises(ValidationError, match="aggregate path data"):
            MediaImportTargetMapping(
                target_root_ref="library-movies",
                files=[
                    MediaImportTargetEntry(
                        source_relative_path="long-source",
                        target_relative_path="long-target",
                    )
                ],
            )
        with pytest.raises(ValidationError, match="aggregate path data"):
            MediaImportInspectionSnapshot(
                download_job_id="job",
                source_root_ref="movies-root",
                target_root_ref="library-movies",
                info_hash_v1=INFO_HASH,
                source_files=[
                    MediaImportInspectedSourceFile(
                        relative_path="long-inspected-source",
                        size_bytes=1,
                        exists=True,
                        is_regular_file=True,
                        is_symlink=False,
                        complete=True,
                    )
                ],
                target_files=[
                    MediaImportInspectedTargetFile(
                        relative_path="target",
                        exists=False,
                    )
                ],
                inspected_at=datetime.now(UTC),
            )

    target_payload = MediaImportTargetMapping(
        target_root_ref="library-movies",
        files=[
            MediaImportTargetEntry(
                source_relative_path="a",
                target_relative_path="b",
            )
        ],
    ).model_dump(mode="json")
    target_payload["overwrite"] = True
    with pytest.raises(ValidationError):
        MediaImportTargetMapping.model_validate(target_payload)


def test_decision_reason_rejects_controls_and_post_sanitize_expansion() -> None:
    for reason in (
        "nul\u0000reason",
        "line\u000abreak",
        "format\u202ereason",
    ):
        with pytest.raises(ValidationError, match="control or format"):
            MediaImportDecisionRequest(reason=reason)

    expanding_reason = "http://a " * 50
    assert len(expanding_reason) <= 500
    with pytest.raises(ValidationError, match="storage limit"):
        MediaImportDecisionRequest(reason=expanding_reason)

    normalized = MediaImportDecisionRequest(reason=" reject http://a ")
    assert normalized.reason == "reject [URL_REDACTED]"
    with pytest.raises(AppError) as service_guard:
        media_import_services._sanitize_reason("internal\u0000reason")
    assert service_guard.value.error_code == "MEDIA_IMPORT_REASON_INVALID"


def test_operation_and_preflight_status_are_closed_by_named_database_checks() -> None:
    plan_type = MediaImportPlan.__table__.c.proposed_operation.type
    preflight_type = MediaImportPreflight.__table__.c.overall_status.type
    assert isinstance(plan_type, SAEnum)
    assert isinstance(preflight_type, SAEnum)
    assert plan_type.create_constraint is True
    assert preflight_type.create_constraint is True
    assert plan_type.name == "ck_media_import_plans_operation_closed"
    assert preflight_type.name == "ck_media_import_preflights_status_closed"

    plan_checks = {
        constraint.name
        for constraint in MediaImportPlan.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    preflight_checks = {
        constraint.name
        for constraint in MediaImportPreflight.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ck_media_import_plans_operation_closed" in plan_checks
    assert "ck_media_import_preflights_status_closed" in preflight_checks


@pytest.mark.asyncio
async def test_create_plan_is_disabled_by_default_and_persists_only_untrusted_proposal(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    job = await seed_ready_job(session_factory, source_item_id="import-create")
    async with session_factory() as session:
        with pytest.raises(AppError) as disabled:
            await create_media_import_request(
                session,
                proposal(job.id),
                import_settings(enable_media_import_control_plane=False),
                actor="operator",
            )
        assert disabled.value.error_code == "MEDIA_IMPORT_DISABLED"

        request, plan = await create_media_import_request(
            session,
            proposal(job.id),
            import_settings(),
            actor="operator",
        )
        await session.commit()

        assert request.status == MediaImportStatus.PREFLIGHT_REQUIRED
        assert plan.mode == "PLAN_ONLY_NO_FILE_OPERATION"
        assert plan.source_retention is True
        assert plan.overwrite_allowed is False
        assert len(plan.plan_hash) == 64
        assert await session.scalar(select(func.count(MediaImportPreflight.id))) == 0
        response = await media_import_request_response(session, request)
        assert response.preflight is None
        assert response.events[0].event_type == "MEDIA_IMPORT_PLAN_PROPOSAL_CREATED"
        assert response.events[0].sanitized_details["trusted_inspection_required"] is True


@pytest.mark.asyncio
async def test_job_eligibility_accepts_completed_seeding_or_paused_at_full_progress(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    job = await seed_ready_job(session_factory, source_item_id="import-status")
    async with session_factory() as session:
        persisted = await session.get(DownloadJob, job.id)
        assert persisted is not None
        persisted.status = DownloadJobStatus.DOWNLOADING
        persisted.progress = 0.99
        await session.commit()
        with pytest.raises(AppError) as not_ready:
            await create_media_import_request(
                session,
                proposal(job.id),
                import_settings(),
                actor="operator",
            )
        assert not_ready.value.error_code == "MEDIA_IMPORT_DOWNLOAD_NOT_READY"

        persisted.status = DownloadJobStatus.PAUSED
        persisted.progress = 1
        await session.commit()

        execution = await session.get(DownloadExecution, job.execution_id)
        assert execution is not None
        execution_id = execution.id
        verified_at = execution.verified_at
        assert verified_at is not None
        await session.execute(
            DownloadExecution.__table__.update()
            .where(DownloadExecution.id == execution_id)
            .values(
                status=DownloadExecutionStatus.RECONCILIATION_REQUIRED,
                verified_at=None,
            )
        )
        await session.commit()
        session.expire_all()
        with pytest.raises(AppError) as execution_not_final:
            await create_media_import_request(
                session,
                proposal(job.id),
                import_settings(),
                actor="operator",
            )
        assert execution_not_final.value.error_code == "MEDIA_IMPORT_EXECUTION_NOT_FINAL"

        await session.execute(
            DownloadExecution.__table__.update()
            .where(DownloadExecution.id == execution_id)
            .values(
                status=DownloadExecutionStatus.SUBMITTED,
                verified_at=verified_at,
            )
        )
        await session.commit()
        session.expire_all()
        request, _ = await create_media_import_request(
            session,
            proposal(job.id),
            import_settings(),
            actor="operator",
        )
        assert request.status == MediaImportStatus.PREFLIGHT_REQUIRED


@pytest.mark.asyncio
async def test_trusted_preflight_is_append_only_and_expiry_requires_a_fresh_preflight(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await seed_ready_job(session_factory, source_item_id="import-preflight")
    settings = import_settings(media_import_preflight_max_age_seconds=30)
    async with session_factory() as session:
        request, _ = await create_media_import_request(
            session, proposal(job.id), settings, actor="operator"
        )
        await session.commit()

        first = await record_media_import_preflight(
            session,
            request.id,
            trusted_inspection(job.id),
            settings,
            actor="system:media-import-inspector",
        )
        await session.commit()
        assert first.overall_status == PreflightStatus.WARNING
        assert request.status == MediaImportStatus.REVIEW_REQUIRED

        future = first.checked_at + timedelta(seconds=31)
        monkeypatch.setattr(media_import_services, "utc_now", lambda: future)
        with pytest.raises(AppError) as stale:
            await approve_media_import_request(
                session,
                request.id,
                MediaImportApproveRequest(
                    acknowledges_plan_only=True,
                    acknowledges_source_retention=True,
                    acknowledges_no_overwrite=True,
                    acknowledges_hnr=True,
                ),
                settings,
                actor="admin",
            )
        assert stale.value.error_code == "MEDIA_IMPORT_PREFLIGHT_STALE"

        refreshed_at = future + timedelta(seconds=1)
        monkeypatch.setattr(media_import_services, "utc_now", lambda: refreshed_at)
        second = await record_media_import_preflight(
            session,
            request.id,
            trusted_inspection(job.id, inspected_at=refreshed_at),
            settings,
            actor="system:media-import-inspector",
        )
        await session.commit()
        assert second.id != first.id
        assert request.status == MediaImportStatus.REVIEW_REQUIRED
        assert await session.scalar(select(func.count(MediaImportPreflight.id))) == 2

        approved = await approve_media_import_request(
            session,
            request.id,
            MediaImportApproveRequest(
                acknowledges_plan_only=True,
                acknowledges_source_retention=True,
                acknowledges_no_overwrite=True,
                acknowledges_hnr=True,
            ),
            settings,
            actor="admin",
        )
        await session.commit()
        assert approved.status == MediaImportStatus.APPROVED_PLAN_ONLY
        response = await media_import_request_response(session, approved)
        assert response.preflight is not None
        assert response.preflight.id == second.id
        assert response.decision_acknowledgements is not None
        approved_event = next(
            event
            for event in response.events
            if event.event_type == "MEDIA_IMPORT_PLAN_ONLY_APPROVED"
        )
        assert approved_event.sanitized_details["execution_authorized"] is False


@pytest.mark.asyncio
async def test_approval_ttl_starts_at_the_trusted_inspection_timestamp(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await seed_ready_job(session_factory, source_item_id="import-observation-ttl")
    settings = import_settings(media_import_preflight_max_age_seconds=30)
    checked_at = datetime.now(UTC)
    inspected_at = checked_at - timedelta(seconds=29)
    monkeypatch.setattr(media_import_services, "utc_now", lambda: checked_at)

    async with session_factory() as session:
        request, _ = await create_media_import_request(
            session, proposal(job.id), settings, actor="operator"
        )
        await record_media_import_preflight(
            session,
            request.id,
            trusted_inspection(job.id, inspected_at=inspected_at),
            settings,
            actor="system:media-import-inspector",
        )
        await session.commit()

        monkeypatch.setattr(
            media_import_services,
            "utc_now",
            lambda: checked_at - timedelta(seconds=1),
        )
        with pytest.raises(AppError) as future_check:
            await approve_media_import_request(
                session,
                request.id,
                approval_confirmation(),
                settings,
                actor="admin",
            )
        assert future_check.value.error_code == "MEDIA_IMPORT_PREFLIGHT_STALE"

        monkeypatch.setattr(
            media_import_services,
            "utc_now",
            lambda: checked_at + timedelta(seconds=2),
        )
        with pytest.raises(AppError) as stale_observation:
            await approve_media_import_request(
                session,
                request.id,
                approval_confirmation(),
                settings,
                actor="admin",
            )
        assert stale_observation.value.error_code == "MEDIA_IMPORT_PREFLIGHT_STALE"


@pytest.mark.asyncio
async def test_blocked_preflight_returns_to_required_and_hnr_confirmation_is_conditional(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    job = await seed_ready_job(session_factory, source_item_id="import-blocked")
    settings = import_settings()
    async with session_factory() as session:
        request, _ = await create_media_import_request(
            session, proposal(job.id), settings, actor="operator"
        )
        await record_media_import_preflight(
            session,
            request.id,
            trusted_inspection(job.id, target_exists=True),
            settings,
            actor="system:media-import-inspector",
        )
        await session.commit()
        assert request.status == MediaImportStatus.PREFLIGHT_REQUIRED

        clean = await record_media_import_preflight(
            session,
            request.id,
            trusted_inspection(job.id),
            settings,
            actor="system:media-import-inspector",
        )
        await session.commit()
        assert clean.overall_status == PreflightStatus.WARNING
        assert request.status == MediaImportStatus.REVIEW_REQUIRED

        with pytest.raises(AppError) as missing_hnr:
            await approve_media_import_request(
                session,
                request.id,
                MediaImportApproveRequest(
                    acknowledges_plan_only=True,
                    acknowledges_source_retention=True,
                    acknowledges_no_overwrite=True,
                ),
                settings,
                actor="admin",
            )
        assert missing_hnr.value.error_code == "MEDIA_IMPORT_HNR_CONFIRMATION_REQUIRED"

        approved = await approve_media_import_request(
            session,
            request.id,
            MediaImportApproveRequest(
                acknowledges_plan_only=True,
                acknowledges_source_retention=True,
                acknowledges_no_overwrite=True,
                acknowledges_hnr=True,
            ),
            settings,
            actor="admin",
        )
        assert approved.status == MediaImportStatus.APPROVED_PLAN_ONLY
        revoked = await revoke_media_import_request(
            session,
            request.id,
            MediaImportDecisionRequest(reason="mapping changed"),
            settings,
            actor="admin",
        )
        assert revoked.status == MediaImportStatus.REVOKED


@pytest.mark.parametrize(
    ("operation", "inspection_overrides", "check_code", "expected_status"),
    [
        pytest.param(
            MediaImportOperation.HARDLINK,
            {"source_exists": False},
            "SOURCE_FILES_SAFE",
            PreflightStatus.BLOCKED,
            id="source-missing",
        ),
        pytest.param(
            MediaImportOperation.HARDLINK,
            {"source_regular": False},
            "SOURCE_FILES_SAFE",
            PreflightStatus.BLOCKED,
            id="source-not-regular",
        ),
        pytest.param(
            MediaImportOperation.HARDLINK,
            {"source_symlink": True},
            "SOURCE_FILES_SAFE",
            PreflightStatus.BLOCKED,
            id="source-symlink",
        ),
        pytest.param(
            MediaImportOperation.HARDLINK,
            {"source_complete": False},
            "SOURCE_FILES_COMPLETE",
            PreflightStatus.BLOCKED,
            id="source-incomplete",
        ),
        pytest.param(
            MediaImportOperation.HARDLINK,
            {"target_exists": True},
            "TARGET_COLLISION_FREE",
            PreflightStatus.BLOCKED,
            id="target-exists",
        ),
        pytest.param(
            MediaImportOperation.HARDLINK,
            {"same_filesystem": False},
            "OPERATION_CAPABILITY",
            PreflightStatus.BLOCKED,
            id="hardlink-cross-filesystem",
        ),
        pytest.param(
            MediaImportOperation.HARDLINK,
            {"same_filesystem": None},
            "OPERATION_CAPABILITY",
            PreflightStatus.UNKNOWN,
            id="hardlink-filesystem-unknown",
        ),
        pytest.param(
            MediaImportOperation.COPY,
            {"available_bytes": 1023},
            "OPERATION_CAPABILITY",
            PreflightStatus.BLOCKED,
            id="copy-space-insufficient",
        ),
        pytest.param(
            MediaImportOperation.COPY,
            {"available_bytes": None},
            "OPERATION_CAPABILITY",
            PreflightStatus.UNKNOWN,
            id="copy-space-unknown",
        ),
    ],
)
@pytest.mark.asyncio
async def test_trusted_preflight_blocks_unsafe_source_target_and_operation_states(
    session_factory: async_sessionmaker[AsyncSession],
    operation: MediaImportOperation,
    inspection_overrides: dict[str, object],
    check_code: str,
    expected_status: PreflightStatus,
) -> None:
    job = await seed_ready_job(
        session_factory,
        source_item_id=f"import-risk-{check_code}-{expected_status.value}",
    )
    settings = import_settings()
    async with session_factory() as session:
        request, _ = await create_media_import_request(
            session,
            proposal(job.id, operation=operation),
            settings,
            actor="operator",
        )
        preflight = await record_media_import_preflight(
            session,
            request.id,
            trusted_inspection(job.id, **inspection_overrides),
            settings,
            actor="system:media-import-inspector",
        )

        result = MediaImportPreflightResult.model_validate(preflight.result)
        checks = {check.code: check.status for check in result.checks}
        assert checks[check_code] == expected_status
        assert preflight.overall_status == expected_status
        assert request.status == MediaImportStatus.PREFLIGHT_REQUIRED


@pytest.mark.asyncio
async def test_inspection_must_exactly_bind_the_untrusted_proposal(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    job = await seed_ready_job(session_factory, source_item_id="import-binding")
    settings = import_settings()
    async with session_factory() as session:
        request, _ = await create_media_import_request(
            session, proposal(job.id), settings, actor="operator"
        )
        changed = trusted_inspection(job.id).model_copy(
            update={
                "source_files": [
                    MediaImportInspectedSourceFile(
                        relative_path="Different.mkv",
                        size_bytes=1024,
                        exists=True,
                        is_regular_file=True,
                        is_symlink=False,
                        complete=True,
                    )
                ]
            }
        )
        with pytest.raises(AppError) as mismatch:
            await record_media_import_preflight(
                session,
                request.id,
                changed,
                settings,
                actor="system:media-import-inspector",
            )
        assert mismatch.value.error_code == "MEDIA_IMPORT_INSPECTION_BINDING_INVALID"
        assert await session.scalar(select(func.count(MediaImportPreflight.id))) == 0

        changed_hash = trusted_inspection(job.id).model_copy(
            update={"info_hash_v1": "f" * 40}
        )
        with pytest.raises(AppError) as hash_mismatch:
            await record_media_import_preflight(
                session,
                request.id,
                changed_hash,
                settings,
                actor="system:media-import-inspector",
            )
        assert hash_mismatch.value.error_code == "MEDIA_IMPORT_INSPECTION_BINDING_INVALID"
        assert await session.scalar(select(func.count(MediaImportPreflight.id))) == 0


@pytest.mark.asyncio
async def test_preflight_and_approval_lock_the_bound_download_state(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await seed_ready_job(session_factory, source_item_id="import-locking")
    settings = import_settings()
    async with session_factory() as session:
        request, _ = await create_media_import_request(
            session, proposal(job.id), settings, actor="operator"
        )
        await session.commit()

        original = media_import_services._load_bound_entities
        lock_requests: list[bool] = []

        async def audited_load_bound_entities(
            current_session: AsyncSession,
            download_job_id: str,
            *,
            for_update: bool = False,
        ):
            lock_requests.append(for_update)
            return await original(
                current_session,
                download_job_id,
                for_update=for_update,
            )

        monkeypatch.setattr(
            media_import_services,
            "_load_bound_entities",
            audited_load_bound_entities,
        )
        await record_media_import_preflight(
            session,
            request.id,
            trusted_inspection(job.id),
            settings,
            actor="system:media-import-inspector",
        )
        await approve_media_import_request(
            session,
            request.id,
            approval_confirmation(),
            settings,
            actor="admin",
        )

        assert lock_requests == [True, True]


@pytest.mark.asyncio
async def test_config_plan_and_current_summary_drift_fail_closed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = import_settings()

    config_job = await seed_ready_job(session_factory, source_item_id="import-config-drift")
    async with session_factory() as session:
        request, _ = await create_media_import_request(
            session, proposal(config_job.id), settings, actor="operator"
        )
        await record_media_import_preflight(
            session,
            request.id,
            trusted_inspection(config_job.id),
            settings,
            actor="system:media-import-inspector",
        )
        await session.commit()

        with pytest.raises(AppError) as config_changed:
            await approve_media_import_request(
                session,
                request.id,
                approval_confirmation(),
                import_settings(media_import_preflight_max_age_seconds=301),
                actor="admin",
            )
        assert config_changed.value.error_code == "MEDIA_IMPORT_CONFIG_CHANGED"

    plan_job = await seed_ready_job(session_factory, source_item_id="import-plan-drift")
    async with session_factory() as session:
        request, plan = await create_media_import_request(
            session, proposal(plan_job.id), settings, actor="operator"
        )
        await record_media_import_preflight(
            session,
            request.id,
            trusted_inspection(plan_job.id),
            settings,
            actor="system:media-import-inspector",
        )
        request_id = request.id
        plan_id = plan.id
        await session.commit()
        await session.execute(
            MediaImportPlan.__table__.update()
            .where(MediaImportPlan.id == plan_id)
            .values(plan_hash="0" * 64)
        )
        await session.commit()
        session.expire_all()

        with pytest.raises(AppError) as plan_changed:
            await approve_media_import_request(
                session,
                request_id,
                approval_confirmation(),
                settings,
                actor="admin",
            )
        assert plan_changed.value.error_code == "MEDIA_IMPORT_PLAN_TAMPERED"

    summary_job = await seed_ready_job(
        session_factory, source_item_id="import-summary-drift"
    )
    async with session_factory() as session:
        request, _ = await create_media_import_request(
            session, proposal(summary_job.id), settings, actor="operator"
        )
        await record_media_import_preflight(
            session,
            request.id,
            trusted_inspection(summary_job.id),
            settings,
            actor="system:media-import-inspector",
        )
        await session.commit()
        persisted_job = await session.get(DownloadJob, summary_job.id)
        assert persisted_job is not None
        persisted_job.hnr_status = HnrStatus.SATISFIED
        await session.commit()

        with pytest.raises(AppError) as summary_changed:
            await approve_media_import_request(
                session,
                request.id,
                approval_confirmation(),
                settings,
                actor="admin",
            )
        assert summary_changed.value.error_code == "MEDIA_IMPORT_SUMMARY_CHANGED"


@pytest.mark.asyncio
async def test_reject_releases_active_job_slot(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    job = await seed_ready_job(session_factory, source_item_id="import-immutable")
    settings = import_settings()
    async with session_factory() as session:
        request, _ = await create_media_import_request(
            session, proposal(job.id), settings, actor="operator"
        )
        await session.commit()
        rejected = await reject_media_import_request(
            session,
            request.id,
            MediaImportDecisionRequest(reason="not now"),
            settings,
            actor="admin",
        )
        await session.commit()
        assert rejected.status == MediaImportStatus.REJECTED
        replacement, _ = await create_media_import_request(
            session, proposal(job.id), settings, actor="operator"
        )
        await session.commit()
        assert replacement.id != request.id


@pytest.mark.asyncio
async def test_request_plan_preflight_and_event_records_are_orm_immutable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    job = await seed_ready_job(session_factory, source_item_id="import-orm-immutable")
    settings = import_settings()
    async with session_factory() as session:
        request, plan = await create_media_import_request(
            session, proposal(job.id), settings, actor="operator"
        )
        preflight = await record_media_import_preflight(
            session,
            request.id,
            trusted_inspection(job.id),
            settings,
            actor="system:media-import-inspector",
        )
        await session.commit()
        event = await session.scalar(
            select(MediaImportEvent)
            .where(MediaImportEvent.request_id == request.id)
            .limit(1)
        )
        assert event is not None
        record_ids = {
            MediaImportRequest: request.id,
            MediaImportPlan: plan.id,
            MediaImportPreflight: preflight.id,
            MediaImportEvent: event.id,
        }

        update_cases = (
            (MediaImportRequest, "requested_by", "tampered-request"),
            (MediaImportPlan, "mode", "MUTATED"),
            (MediaImportPreflight, "checked_by", "tampered-preflight"),
            (MediaImportEvent, "actor", "tampered-event"),
        )
        for model, field, value in update_cases:
            record = await session.get(model, record_ids[model])
            assert record is not None
            setattr(record, field, value)
            with pytest.raises(ValueError, match="media import"):
                await session.flush()
            await session.rollback()

        for model, record_id in record_ids.items():
            record = await session.get(model, record_id)
            assert record is not None
            await session.delete(record)
            with pytest.raises(ValueError, match="media import"):
                await session.flush()
            await session.rollback()


@pytest.mark.asyncio
async def test_terminal_request_cannot_fill_an_initially_empty_decision_reason(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = import_settings()
    rejected_job = await seed_ready_job(
        session_factory, source_item_id="import-terminal-rejected"
    )
    async with session_factory() as session:
        request, _ = await create_media_import_request(
            session, proposal(rejected_job.id), settings, actor="operator"
        )
        rejected = await reject_media_import_request(
            session,
            request.id,
            MediaImportDecisionRequest(),
            settings,
            actor="admin",
        )
        await session.commit()
        assert rejected.rejection_reason is None
        rejected.rejection_reason = "late reason"
        with pytest.raises(ValueError, match="terminal media import"):
            await session.flush()
        await session.rollback()

    revoked_job = await seed_ready_job(
        session_factory, source_item_id="import-terminal-revoked"
    )
    async with session_factory() as session:
        request, _ = await create_media_import_request(
            session, proposal(revoked_job.id), settings, actor="operator"
        )
        await record_media_import_preflight(
            session,
            request.id,
            trusted_inspection(revoked_job.id),
            settings,
            actor="system:media-import-inspector",
        )
        approved = await approve_media_import_request(
            session,
            request.id,
            approval_confirmation(),
            settings,
            actor="admin",
        )
        revoked = await revoke_media_import_request(
            session,
            approved.id,
            MediaImportDecisionRequest(),
            settings,
            actor="admin",
        )
        await session.commit()
        assert revoked.revocation_reason is None
        revoked.revocation_reason = "late reason"
        with pytest.raises(ValueError, match="terminal media import"):
            await session.flush()


@pytest.fixture
def media_import_api_client_factory(
    session_factory: async_sessionmaker[AsyncSession],
):
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    principal = Principal(
        username="api-admin",
        role=AuthRole.ADMIN,
        issued_at=0,
        expires_at=2**31,
        csrf_digest="0" * 64,
    )

    async def override_principal() -> Principal:
        return principal

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_viewer_principal] = override_principal
    app.dependency_overrides[get_operator_principal] = override_principal
    app.dependency_overrides[get_admin_principal] = override_principal

    def factory(settings: Settings) -> httpx.AsyncClient:
        app.dependency_overrides[get_settings] = lambda: settings
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )

    yield factory
    app.dependency_overrides.clear()


@pytest.fixture
def authenticated_media_import_client_factory(
    session_factory: async_sessionmaker[AsyncSession],
):
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_session] = override_session

    def factory(role: AuthRole) -> httpx.AsyncClient:
        settings = authenticated_import_settings(role)
        app.dependency_overrides[get_settings] = lambda: settings
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )

    yield factory
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_api_is_gated_strict_and_has_no_preflight_or_execution_endpoint(
    session_factory: async_sessionmaker[AsyncSession],
    media_import_api_client_factory,
) -> None:
    job = await seed_ready_job(session_factory, source_item_id="import-api")
    disabled = import_settings(enable_media_import_control_plane=False)
    async with media_import_api_client_factory(disabled) as client:
        response = await client.get("/api/media-import-requests")
    assert response.status_code == 409
    assert response.json()["error_code"] == "MEDIA_IMPORT_DISABLED"

    payload = proposal(job.id).model_dump(mode="json")
    payload["inspection_snapshot"] = trusted_inspection(job.id).model_dump(mode="json")
    async with media_import_api_client_factory(import_settings()) as client:
        forged = await client.post("/api/media-import-requests", json=payload)
        created = await client.post(
            "/api/media-import-requests",
            json=proposal(job.id).model_dump(mode="json"),
        )
        listed = await client.get("/api/media-import-requests?page=1&page_size=10")
        detail = await client.get(
            f"/api/media-import-requests/{created.json().get('id', 'missing')}"
        )
    assert forged.status_code == 422
    assert created.status_code == 201
    assert created.json()["status"] == "PREFLIGHT_REQUIRED"
    assert created.json()["preflight"] is None
    assert listed.status_code == 200 and listed.json()["total"] == 1
    assert detail.status_code == 200
    assert detail.json()["id"] == created.json()["id"]

    openapi = app.openapi()
    paths = {
        path: set(methods)
        for path, methods in openapi["paths"].items()
        if "media-import" in path
    }
    assert paths == {
        "/api/media-import-requests": {"get", "post"},
        "/api/media-import-requests/{request_id}": {"get"},
        "/api/media-import-requests/{request_id}/approve": {"post"},
        "/api/media-import-requests/{request_id}/reject": {"post"},
        "/api/media-import-requests/{request_id}/revoke": {"post"},
    }
    assert not any(
        forbidden in path
        for path in paths
        for forbidden in ("preflight", "execute", "scan", "writeback")
    )


@pytest.mark.asyncio
async def test_list_is_a_two_query_bounded_summary_while_detail_stays_complete(
    session_factory: async_sessionmaker[AsyncSession],
    media_import_api_client_factory,
) -> None:
    first_job = await seed_ready_job(
        session_factory, source_item_id="import-list-summary-1"
    )
    second_job = await seed_ready_job(
        session_factory, source_item_id="import-list-summary-2"
    )
    settings = import_settings()
    async with session_factory() as session:
        first_request, first_plan = await create_media_import_request(
            session, proposal(first_job.id), settings, actor="operator"
        )
        await record_media_import_preflight(
            session,
            first_request.id,
            trusted_inspection(first_job.id, target_exists=True),
            settings,
            actor="system:media-import-inspector",
        )
        latest_preflight = await record_media_import_preflight(
            session,
            first_request.id,
            trusted_inspection(first_job.id),
            settings,
            actor="system:media-import-inspector",
        )
        second_request, _ = await create_media_import_request(
            session, proposal(second_job.id), settings, actor="operator"
        )
        expected_media_title = str(
            first_plan.job_summary_snapshot["media_title"]
        )
        await session.execute(
            MediaItem.__table__.update()
            .where(MediaItem.id == first_request.media_item_id)
            .values(
                title="DRIFTED LIVE MEDIA",
                year=1999,
                tmdb_id=999999,
            )
        )
        await session.execute(
            DownloadJob.__table__.update()
            .where(DownloadJob.id == first_job.id)
            .values(
                save_path_ref="drifted-live-root",
                file_count=2,
                size_bytes=2048,
            )
        )
        await session.commit()
        first_request_id = first_request.id
        second_request_id = second_request.id
        first_plan_id = first_plan.id
        latest_preflight_hash = latest_preflight.preflight_hash

    statements: list[str] = []

    def capture_selects(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    bind = session_factory.kw["bind"]
    sqlalchemy_event.listen(bind.sync_engine, "before_cursor_execute", capture_selects)
    try:
        async with media_import_api_client_factory(settings) as client:
            listed = await client.get(
                "/api/media-import-requests?page=1&page_size=100"
            )
    finally:
        sqlalchemy_event.remove(
            bind.sync_engine,
            "before_cursor_execute",
            capture_selects,
        )

    assert listed.status_code == 200
    assert len(statements) == 2
    payload = listed.json()
    assert payload["total"] == 2
    assert {item["id"] for item in payload["items"]} == {
        first_request_id,
        second_request_id,
    }
    first_summary = next(
        item for item in payload["items"] if item["id"] == first_request_id
    )
    assert set(first_summary) == {
        "id",
        "download_job_id",
        "media_item_id",
        "execution_id",
        "status",
        "requested_by",
        "requested_at",
        "updated_at",
        "plan_id",
        "mode",
        "proposed_operation",
        "plan_hash",
        "media_type",
        "tmdb_id",
        "media_title",
        "media_year",
        "source_root_ref",
        "target_root_ref",
        "file_count",
        "size_bytes",
        "preflight_status",
        "preflight_checked_at",
        "preflight_hash",
    }
    assert first_summary["plan_id"] == first_plan_id
    assert first_summary["source_root_ref"] == "movies-root"
    assert first_summary["target_root_ref"] == "library-movies"
    assert first_summary["media_title"] == expected_media_title
    assert first_summary["media_year"] == 2026
    assert first_summary["file_count"] == 1
    assert first_summary["size_bytes"] == 1024
    assert first_summary["preflight_status"] == "WARNING"
    assert first_summary["preflight_hash"] == latest_preflight_hash
    assert not {
        "source_manifest",
        "target_mapping",
        "inspection_snapshot",
        "result",
        "events",
    }.intersection(first_summary)

    async with media_import_api_client_factory(settings) as client:
        detail = await client.get(
            f"/api/media-import-requests/{first_request_id}"
        )
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["plan"]["source_manifest"]["files"]
    assert detail_payload["plan"]["target_mapping"]["files"]
    assert detail_payload["preflight"]["result"]["checks"]
    assert detail_payload["events"]


@pytest.mark.asyncio
async def test_api_role_matrix_limits_mutation_and_decisions(
    session_factory: async_sessionmaker[AsyncSession],
    authenticated_media_import_client_factory,
) -> None:
    job = await seed_ready_job(session_factory, source_item_id="import-role-matrix")
    create_payload = proposal(job.id).model_dump(mode="json")

    async with authenticated_media_import_client_factory(AuthRole.VIEWER) as client:
        csrf = await login_for_media_import(client)
        readable = await client.get("/api/media-import-requests")
        create_denied = await client.post(
            "/api/media-import-requests",
            json=create_payload,
            headers={CSRF_HEADER_NAME: csrf},
        )
        decision_denied = await client.post(
            "/api/media-import-requests/missing/reject",
            json={},
            headers={CSRF_HEADER_NAME: csrf},
        )
    assert readable.status_code == 200
    for denied in (create_denied, decision_denied):
        assert denied.status_code == 403
        assert denied.json()["error_code"] == "AUTH_ROLE_FORBIDDEN"

    async with authenticated_media_import_client_factory(AuthRole.OPERATOR) as client:
        csrf = await login_for_media_import(client)
        created = await client.post(
            "/api/media-import-requests",
            json=create_payload,
            headers={CSRF_HEADER_NAME: csrf},
        )
        assert created.status_code == 201
        request_id = created.json()["id"]
        approve_denied = await client.post(
            f"/api/media-import-requests/{request_id}/approve",
            json=approval_confirmation().model_dump(mode="json"),
            headers={CSRF_HEADER_NAME: csrf},
        )
        reject_denied = await client.post(
            f"/api/media-import-requests/{request_id}/reject",
            json={},
            headers={CSRF_HEADER_NAME: csrf},
        )
        revoke_denied = await client.post(
            f"/api/media-import-requests/{request_id}/revoke",
            json={},
            headers={CSRF_HEADER_NAME: csrf},
        )
    for denied in (approve_denied, reject_denied, revoke_denied):
        assert denied.status_code == 403
        assert denied.json()["error_code"] == "AUTH_ROLE_FORBIDDEN"

    async with authenticated_media_import_client_factory(AuthRole.ADMIN) as client:
        csrf = await login_for_media_import(client)
        rejected = await client.post(
            f"/api/media-import-requests/{request_id}/reject",
            json={"reason": "admin decision"},
            headers={CSRF_HEADER_NAME: csrf},
        )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "REJECTED"
    assert rejected.json()["rejected_by"] == AUTH_USERNAME


@pytest.mark.asyncio
async def test_plan_preflight_and_approval_do_not_invoke_file_mutation_primitives(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await seed_ready_job(session_factory, source_item_id="import-no-file-io")

    def reject_file_mutation(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("media import control plane attempted a file operation")

    for module, names in (
        (builtins, ("open",)),
        (os, ("link", "remove", "rename", "replace", "unlink")),
        (
            shutil,
            ("copy", "copy2", "copyfile", "copytree", "move", "rmtree"),
        ),
        (
            Path,
            (
                "mkdir",
                "rename",
                "replace",
                "touch",
                "unlink",
                "write_bytes",
                "write_text",
            ),
        ),
    ):
        for name in names:
            monkeypatch.setattr(module, name, reject_file_mutation)

    settings = import_settings()
    async with session_factory() as session:
        request, _ = await create_media_import_request(
            session, proposal(job.id), settings, actor="operator"
        )
        await record_media_import_preflight(
            session,
            request.id,
            trusted_inspection(job.id),
            settings,
            actor="system:media-import-inspector",
        )
        approved = await approve_media_import_request(
            session,
            request.id,
            approval_confirmation(),
            settings,
            actor="admin",
        )
        assert approved.status == MediaImportStatus.APPROVED_PLAN_ONLY


def test_media_import_service_has_no_file_mutation_primitive() -> None:
    source = inspect.getsource(media_import_services)
    for forbidden in (
        "os.link(",
        "shutil.copy",
        ".rename(",
        ".unlink(",
        "os.remove(",
        "os.replace(",
    ):
        assert forbidden not in source
