from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import NoReturn

import bencodepy  # type: ignore[import-untyped]
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.downloaders.qbittorrent import QbAddResult
from app.core.config import Settings
from app.errors import AppError
from app.models.entities import (
    ApprovalRequest,
    DownloadExecution,
    DownloadExecutionEvent,
    DownloadJob,
    DownloadPlan,
    MediaItem,
    TorrentCandidateRecord,
    TorrentSearchRun,
)
from app.models.enums import (
    ApprovalStatus,
    DownloadExecutionStatus,
    DownloadJobStatus,
    HnrStatus,
    IdentityConfidence,
    MediaType,
    MetadataStatus,
    PreflightStatus,
    WorkflowStatus,
)
from app.schemas.adapters import TorrentCandidate, TorrentSearchRequest
from app.schemas.approvals import (
    ApprovalCandidateSnapshot,
    MediaDestinationPlan,
    PreflightCheck,
    PreflightResult,
    PromotionSnapshot,
)
from app.schemas.executions import (
    DownloadExecutionCreateRequest,
    ExecutionIntentCreateRequest,
)
from app.schemas.qbittorrent import QbTorrent
from app.services.approvals import download_plan_hash, snapshot_hash
from app.services.executions import create_execution_intent, execute_approved_plan
from app.workers.download_executor import (
    DownloadExecutor,
    DownloadMonitor,
)

RELEASE_TITLE = "Execution Movie 2026 1080p WEB-DL"
TORRENT_ID = "approved-torrent-42"
SECRET_URL = "https://avistaz.to/download?pid=secret-pid&passkey=secret-passkey"


def torrent_fixture() -> tuple[bytes, str]:
    info = {
        b"length": 1024,
        b"name": b"Execution.Movie.2026.mkv",
        b"piece length": 16_384,
        b"pieces": b"p" * 20,
    }
    payload = bencodepy.encode({b"info": info})
    return payload, hashlib.sha1(bencodepy.encode(info)).hexdigest()


def executor_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "enable_download_execution_control_plane": True,
        "enable_download_executor": True,
        "enable_avistaz_torrent_fetch": True,
        "enable_qb_write": True,
        "enable_qb_read_only": True,
        "enable_download_monitor": True,
        "qb_base_url": "https://qb.internal.test",
        "qb_allowed_hosts": ("qb.internal.test",),
        "qb_target_save_path": "/downloads/movies/incoming",
        "qb_allowed_save_paths": ("/downloads/movies",),
        "qb_target_category": "movies",
        "qb_save_path_ref": "movies-root",
        "qb_plan_tags": ("unin-plan",),
        "qb_target_instance_ref": "qb-primary",
        "download_execution_lease_seconds": 30,
        "download_execution_lease_renew_interval_seconds": 1,
        "download_execution_retry_base_seconds": 1,
        "download_execution_retry_max_seconds": 4,
        "download_monitor_batch_size": 20,
        "job_max_attempts": 3,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def approved_candidate(info_hash: str) -> TorrentCandidate:
    return TorrentCandidate(
        site_id="avistaz",
        torrent_id=TORRENT_ID,
        release_title=RELEASE_TITLE,
        details_ref="avistaz:details:approved-fixed-ref",
        media_type=MediaType.MOVIE,
        tmdb_id=424242,
        year=2026,
        size_bytes=1024,
        file_count=1,
        seeders=12,
        download_factor=0,
        upload_factor=1,
        hit_and_run=False,
        info_hash=info_hash,
    )


def qb_observation(
    info_hash: str,
    *,
    state: str = "downloading",
    progress: float = 0.25,
) -> QbTorrent:
    return QbTorrent(
        hash=info_hash,
        infohash_v1=info_hash,
        name=RELEASE_TITLE,
        size=1024,
        progress=progress,
        ratio=0.5,
        state=state,
        added_on=1_700_000_000,
        downloaded=int(1024 * progress),
        uploaded=128,
        dlspeed=2048,
        upspeed=512,
        category="movies",
        tags="unin-plan",
        save_path="/downloads/movies/incoming",
    )


class FakeAvistaZ:
    def __init__(
        self,
        candidates: list[TorrentCandidate],
        payload: bytes,
        *,
        search_error: AppError | None = None,
    ) -> None:
        self.candidates = candidates
        self.payload = payload
        self.search_error = search_error
        self.search_calls: list[TorrentSearchRequest] = []
        self.fetch_calls: list[str] = []
        self.closed = False

    async def search(self, request: TorrentSearchRequest) -> list[TorrentCandidate]:
        self.search_calls.append(request)
        if self.search_error is not None:
            raise self.search_error
        return self.candidates

    async def fetch_torrent(self, torrent_id: str) -> bytes:
        self.fetch_calls.append(torrent_id)
        return self.payload

    async def aclose(self) -> None:
        self.closed = True


class FakeQb:
    def __init__(
        self,
        list_results: list[list[QbTorrent]],
        *,
        add_result: QbAddResult | None = None,
        add_error: AppError | None = None,
    ) -> None:
        self.list_results = list_results
        self.add_result = add_result
        self.add_error = add_error
        self.calls: list[str] = []
        self.added_payloads: list[bytes] = []

    async def authenticate(self) -> None:
        self.calls.append("authenticate")

    async def list_torrents(self) -> list[QbTorrent]:
        self.calls.append("list_torrents")
        index = min(self.calls.count("list_torrents") - 1, len(self.list_results) - 1)
        return self.list_results[index]

    async def add_torrent(
        self,
        torrent: bytes,
        *,
        expected_info_hash: str,
        save_path: str,
        category: str,
        tags: tuple[str, ...] = (),
        start_immediately: bool = True,
        write_guard: Callable[[], Awaitable[None]] | None = None,
    ) -> QbAddResult:
        del expected_info_hash, save_path, category, tags, start_immediately
        if write_guard is not None:
            await write_guard()
        self.calls.append("add_torrent")
        self.added_payloads.append(torrent)
        if self.add_error is not None:
            raise self.add_error
        if self.add_result is None:
            raise AssertionError("add result was not configured")
        return self.add_result

    async def aclose(self) -> None:
        self.calls.append("close")


class CrashingQb(FakeQb):
    async def authenticate(self) -> NoReturn:
        self.calls.append("authenticate")
        raise KeyboardInterrupt("simulated process crash")


async def seed_pending_execution(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    info_hash: str,
) -> str:
    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=1)
    async with session_factory() as session:
        media = MediaItem(
            source="nextfind",
            source_item_id=f"executor-media-{info_hash[:8]}",
            media_type=MediaType.MOVIE,
            tmdb_id=424242,
            title="Execution Movie",
            year=2026,
            identity_confidence=IdentityConfidence.HIGH,
            metadata_status=MetadataStatus.RESOLVED,
            workflow_status=WorkflowStatus.TORRENT_REVIEW,
            discovered_at=now,
            updated_at=now,
        )
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
            torrent_id=TORRENT_ID,
            candidate_snapshot={},
            match_score=0.99,
            match_reasons=["TMDB_ID_EXACT"],
            warnings=[],
        )
        session.add(candidate)
        await session.flush()
        snapshot = ApprovalCandidateSnapshot(
            media_item_id=media.id,
            media_title=media.title,
            media_type=media.media_type,
            tmdb_id=media.tmdb_id,
            year=media.year,
            torrent_candidate_id=candidate.id,
            site_id="avistaz",
            torrent_id=TORRENT_ID,
            torrent_ref="avistaz:details:approved-fixed-ref",
            release_title=RELEASE_TITLE,
            size_bytes=1024,
            info_hash=info_hash,
            promotion=PromotionSnapshot(download_factor=0, upload_factor=1),
            hit_and_run=False,
            match_score=0.99,
            match_reasons=["TMDB_ID_EXACT"],
            warnings=[],
            requested_at=now,
            expires_at=expires_at,
        )
        snapshot_data: dict[str, object] = snapshot.model_dump(mode="json")
        preflight = PreflightResult(
            overall_status=PreflightStatus.PASS,
            checks=[
                PreflightCheck(
                    code="QB_CONNECTION", status=PreflightStatus.PASS, message="ok"
                )
            ],
            checked_at=now,
            policy_fingerprint="a" * 64,
        )
        approval = ApprovalRequest(
            media_item_id=media.id,
            torrent_candidate_id=candidate.id,
            status=ApprovalStatus.APPROVED,
            candidate_snapshot=snapshot_data,
            snapshot_hash=snapshot_hash(snapshot_data),
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
            torrent_ref="avistaz:details:approved-fixed-ref",
            expected_info_hash=info_hash,
            release_title=RELEASE_TITLE,
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
        await session.flush()
        intent, nonce = await create_execution_intent(
            session,
            approval.id,
            ExecutionIntentCreateRequest(),
            settings,
            actor="execution-admin",
        )
        execution, created = await execute_approved_plan(
            session,
            approval.id,
            DownloadExecutionCreateRequest(intent_id=intent.id, nonce=nonce),
            f"executor-idempotency-{info_hash}",
            settings,
            actor="execution-admin",
        )
        assert created is True
        await session.commit()
        return execution.id


@pytest.mark.asyncio
async def test_executor_requires_all_three_write_gates_before_claim_or_factory(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    calls = 0

    def forbidden_factory() -> NoReturn:
        nonlocal calls
        calls += 1
        raise AssertionError("adapter factory must not run")

    settings = executor_settings(enable_qb_write=False)
    executor = DownloadExecutor(
        session_factory,
        "executor-test",
        forbidden_factory,
        forbidden_factory,
        settings,
    )
    with pytest.raises(AppError) as caught:
        await executor.run_once()

    assert caught.value.error_code == "DOWNLOAD_EXECUTOR_DISABLED"
    assert caught.value.details == {"missing_flags": ["ENABLE_QB_WRITE"]}
    assert calls == 0


@pytest.mark.asyncio
async def test_executor_submits_only_exact_researched_torrent_and_creates_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload, info_hash = torrent_fixture()
    settings = executor_settings()
    execution_id = await seed_pending_execution(session_factory, settings, info_hash)
    avistaz = FakeAvistaZ([approved_candidate(info_hash)], payload)
    observed = qb_observation(info_hash)
    qb = FakeQb(
        [[], [observed]],
        add_result=QbAddResult(info_hash=info_hash, outcome="SUBMITTED"),
    )
    executor = DownloadExecutor(
        session_factory, "executor-test", lambda: avistaz, lambda: qb, settings
    )

    assert await executor.run_once() is True

    assert len(avistaz.search_calls) == 1
    assert avistaz.search_calls[0].tmdb == 424242
    assert avistaz.fetch_calls == [TORRENT_ID]
    assert qb.calls == [
        "authenticate",
        "list_torrents",
        "add_torrent",
        "list_torrents",
        "close",
    ]
    assert qb.added_payloads == [payload]
    async with session_factory() as session:
        execution = await session.get(DownloadExecution, execution_id)
        job = await session.scalar(
            select(DownloadJob).where(DownloadJob.execution_id == execution_id)
        )
        approval = (
            await session.get(ApprovalRequest, execution.approval_id)
            if execution is not None
            else None
        )
        assert execution is not None
        assert execution.status == DownloadExecutionStatus.SUBMITTED
        assert execution.actual_info_hash_v1 == info_hash
        assert execution.locked_at is None
        assert approval is not None and approval.status == ApprovalStatus.CONSUMED
        assert job is not None
        assert job.status == DownloadJobStatus.DOWNLOADING
        assert job.hnr_status == HnrStatus.UNKNOWN


@pytest.mark.asyncio
async def test_existing_torrent_is_idempotent_and_never_calls_add(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload, info_hash = torrent_fixture()
    settings = executor_settings()
    execution_id = await seed_pending_execution(session_factory, settings, info_hash)
    avistaz = FakeAvistaZ([approved_candidate(info_hash)], payload)
    observed = qb_observation(info_hash)
    qb = FakeQb([[observed], [observed]])
    executor = DownloadExecutor(
        session_factory, "executor-test", lambda: avistaz, lambda: qb, settings
    )

    assert await executor.run_once() is True

    assert "add_torrent" not in qb.calls
    async with session_factory() as session:
        execution = await session.get(DownloadExecution, execution_id)
        assert execution is not None
        assert execution.status == DownloadExecutionStatus.ALREADY_PRESENT


@pytest.mark.asyncio
async def test_same_title_with_different_torrent_id_never_fetches_or_reaches_qb(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload, info_hash = torrent_fixture()
    settings = executor_settings()
    execution_id = await seed_pending_execution(session_factory, settings, info_hash)
    drifted = approved_candidate(info_hash).model_copy(
        update={"torrent_id": "different-torrent-id"}
    )
    avistaz = FakeAvistaZ([drifted], payload)
    qb_factory_calls = 0

    def qb_factory() -> NoReturn:
        nonlocal qb_factory_calls
        qb_factory_calls += 1
        raise AssertionError("fuzzy title match must not reach qB")

    executor = DownloadExecutor(
        session_factory, "executor-test", lambda: avistaz, qb_factory, settings
    )

    assert await executor.run_once() is True

    assert len(avistaz.search_calls) == 1
    assert avistaz.fetch_calls == []
    assert qb_factory_calls == 0
    async with session_factory() as session:
        execution = await session.get(DownloadExecution, execution_id)
        assert execution is not None
        assert execution.status == DownloadExecutionStatus.FAILED
        assert execution.error_code == "AVISTAZ_TORRENT_ID_NOT_UNIQUE"
        assert execution.actual_info_hash is None


@pytest.mark.asyncio
async def test_unknown_add_outcome_is_terminal_and_never_automatically_retried(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload, info_hash = torrent_fixture()
    settings = executor_settings()
    execution_id = await seed_pending_execution(session_factory, settings, info_hash)
    avistaz = FakeAvistaZ([approved_candidate(info_hash)], payload)
    qb = FakeQb(
        [[]],
        add_error=AppError(
            "QB_ADD_OUTCOME_UNKNOWN",
            "qB add result unknown",
            status_code=502,
            details={"external_write_may_have_occurred": True},
        ),
    )
    executor = DownloadExecutor(
        session_factory, "executor-test", lambda: avistaz, lambda: qb, settings
    )

    assert await executor.run_once() is True
    assert await executor.run_once() is False

    assert qb.calls.count("add_torrent") == 1
    async with session_factory() as session:
        execution = await session.get(DownloadExecution, execution_id)
        jobs = list(
            (
                await session.scalars(
                    select(DownloadJob).where(DownloadJob.execution_id == execution_id)
                )
            ).all()
        )
        assert execution is not None
        assert execution.status == DownloadExecutionStatus.OUTCOME_UNKNOWN
        assert execution.next_retry_at is None
        assert execution.lease_token is None
        assert jobs == []


@pytest.mark.asyncio
async def test_retryable_validation_failure_is_sanitized_and_does_not_touch_qb(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload, info_hash = torrent_fixture()
    settings = executor_settings()
    execution_id = await seed_pending_execution(session_factory, settings, info_hash)
    avistaz = FakeAvistaZ(
        [],
        payload,
        search_error=AppError(
            "AVISTAZ_UNAVAILABLE",
            f"temporary failure at {SECRET_URL}",
            status_code=502,
            retryable=True,
        ),
    )
    qb_factory_calls = 0

    def qb_factory() -> NoReturn:
        nonlocal qb_factory_calls
        qb_factory_calls += 1
        raise AssertionError("qB must not be created before reservation")

    executor = DownloadExecutor(
        session_factory, "executor-test", lambda: avistaz, qb_factory, settings
    )

    assert await executor.run_once() is True

    assert qb_factory_calls == 0
    async with session_factory() as session:
        execution = await session.get(DownloadExecution, execution_id)
        events = list(
            (
                await session.scalars(
                    select(DownloadExecutionEvent).where(
                        DownloadExecutionEvent.download_execution_id == execution_id
                    )
                )
            ).all()
        )
        assert execution is not None
        assert execution.status == DownloadExecutionStatus.RETRY_WAIT
        assert execution.actual_info_hash is None
        assert execution.next_retry_at is not None
        serialized = repr(
            [execution.error_message, *[event.sanitized_details for event in events]]
        )
        assert "secret-pid" not in serialized
        assert "secret-passkey" not in serialized
        assert "/download?" not in serialized


@pytest.mark.asyncio
async def test_expired_lease_is_rejected_before_any_external_adapter_action(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload, info_hash = torrent_fixture()
    settings = executor_settings()
    execution_id = await seed_pending_execution(session_factory, settings, info_hash)
    avistaz = FakeAvistaZ([approved_candidate(info_hash)], payload)
    qb = FakeQb([[]])
    executor = DownloadExecutor(
        session_factory, "executor-test", lambda: avistaz, lambda: qb, settings
    )
    claim = await executor._claim_next_execution()
    assert claim is not None
    async with session_factory() as session:
        execution = await session.get(DownloadExecution, execution_id, with_for_update=True)
        assert execution is not None
        execution.locked_at = datetime.now(UTC) - timedelta(seconds=31)
        await session.commit()

    await executor._process_claim(claim)

    assert avistaz.search_calls == []
    assert avistaz.fetch_calls == []
    assert qb.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("approval_status", [ApprovalStatus.REVOKED, ApprovalStatus.EXPIRED])
async def test_invalidated_queued_approval_is_cancelled_before_any_external_action(
    session_factory: async_sessionmaker[AsyncSession],
    approval_status: ApprovalStatus,
) -> None:
    payload, info_hash = torrent_fixture()
    settings = executor_settings()
    execution_id = await seed_pending_execution(session_factory, settings, info_hash)
    async with session_factory() as session:
        execution = await session.get(DownloadExecution, execution_id)
        assert execution is not None
        approval = await session.get(
            ApprovalRequest, execution.approval_id, with_for_update=True
        )
        assert approval is not None
        approval.status = approval_status
        approval.decided_at = datetime.now(UTC)
        await session.commit()
    avistaz = FakeAvistaZ([approved_candidate(info_hash)], payload)
    qb = FakeQb([[]])
    executor = DownloadExecutor(
        session_factory, "executor-test", lambda: avistaz, lambda: qb, settings
    )

    assert await executor.run_once() is True

    assert avistaz.search_calls == []
    assert avistaz.fetch_calls == []
    assert qb.calls == []
    async with session_factory() as session:
        execution = await session.get(DownloadExecution, execution_id)
        events = list(
            (
                await session.scalars(
                    select(DownloadExecutionEvent).where(
                        DownloadExecutionEvent.download_execution_id == execution_id,
                        DownloadExecutionEvent.event_type == "APPROVAL_INVALIDATED",
                    )
                )
            ).all()
        )
        assert execution is not None
        assert execution.status == DownloadExecutionStatus.CANCELLED
        assert execution.lease_token is None
        assert len(events) == 1
        assert events[0].sanitized_details["external_request_performed"] is False


@pytest.mark.asyncio
async def test_crash_after_reservation_becomes_reconciliation_and_never_readds(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload, info_hash = torrent_fixture()
    settings = executor_settings()
    execution_id = await seed_pending_execution(session_factory, settings, info_hash)
    avistaz = FakeAvistaZ([approved_candidate(info_hash)], payload)
    crashing_qb = CrashingQb([[]])
    executor = DownloadExecutor(
        session_factory,
        "executor-test",
        lambda: avistaz,
        lambda: crashing_qb,
        settings,
    )
    claim = await executor._claim_next_execution()
    assert claim is not None
    with pytest.raises(KeyboardInterrupt):
        await executor._process_claim(claim)

    async with session_factory() as session:
        execution = await session.get(DownloadExecution, execution_id, with_for_update=True)
        assert execution is not None
        assert execution.status == DownloadExecutionStatus.SUBMITTING
        execution.locked_at = datetime.now(UTC) - timedelta(seconds=31)
        await session.commit()

    assert await executor.run_once() is True

    async with session_factory() as session:
        execution = await session.get(DownloadExecution, execution_id)
        assert execution is not None
        assert execution.status == DownloadExecutionStatus.RECONCILIATION_REQUIRED
        assert execution.next_retry_at is None
        assert execution.lease_token is None


@pytest.mark.asyncio
async def test_monitor_uses_only_read_methods_and_keeps_hnr_unknown(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload, info_hash = torrent_fixture()
    settings = executor_settings()
    execution_id = await seed_pending_execution(session_factory, settings, info_hash)
    avistaz = FakeAvistaZ([approved_candidate(info_hash)], payload)
    initial = qb_observation(info_hash)
    executor_qb = FakeQb(
        [[], [initial]],
        add_result=QbAddResult(info_hash=info_hash, outcome="SUBMITTED"),
    )
    executor = DownloadExecutor(
        session_factory,
        "executor-test",
        lambda: avistaz,
        lambda: executor_qb,
        settings,
    )
    assert await executor.run_once() is True

    monitor_qb = FakeQb([[qb_observation(info_hash, state="uploading", progress=1)]])
    monitor = DownloadMonitor(
        session_factory,
        "monitor-test",
        lambda: monitor_qb,
        settings,
    )
    assert await monitor.run_once() == 1

    assert monitor_qb.calls == ["authenticate", "list_torrents", "close"]
    async with session_factory() as session:
        job = await session.scalar(
            select(DownloadJob).where(DownloadJob.execution_id == execution_id)
        )
        assert job is not None
        assert job.status == DownloadJobStatus.SEEDING
        assert job.hnr_status == HnrStatus.UNKNOWN
