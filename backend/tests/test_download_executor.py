from __future__ import annotations

import hashlib
import uuid
from collections.abc import Awaitable, Callable, Collection
from datetime import UTC, datetime, timedelta
from typing import NoReturn

import bencodepy  # type: ignore[import-untyped]
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.downloaders.qbittorrent import QbAddResult
from app.adapters.pt_sites.catalog import PtSiteCatalog, PtSiteDeclaration
from app.adapters.pt_sites.execution_registry import (
    PtExecutionAdapter,
    PtExecutionRegistry,
)
from app.core.config import Settings
from app.errors import AppError
from app.models.entities import (
    ApprovalRequest,
    DownloadExecution,
    DownloadExecutionEvent,
    DownloadJob,
    DownloadJobEvent,
    DownloadPlan,
    MediaItem,
    TorrentCandidateRecord,
    TorrentSearchRun,
)
from app.models.enums import (
    ApprovalStatus,
    AutomationMode,
    AutomationStage,
    DecisionOutcome,
    DownloadExecutionStatus,
    DownloadJobStatus,
    DownloadLaunchMode,
    HnrStatus,
    IdentityConfidence,
    MediaType,
    MetadataStatus,
    Origin,
    PreflightStatus,
    WorkflowStatus,
)
from app.schemas.adapters import (
    AdapterManifest,
    PtSearchMode,
    TorrentCandidate,
    TorrentSearchRequest,
)
from app.schemas.approvals import (
    ApprovalCandidateSnapshot,
    MediaDestinationPlan,
    PreflightCheck,
    PreflightResult,
    PromotionSnapshot,
)
from app.schemas.automation import AutomationPolicyRevisionCreateRequest
from app.schemas.executions import (
    DownloadExecutionCreateRequest,
    ExecutionIntentCreateRequest,
)
from app.schemas.qbittorrent import QbTorrent
from app.services.approvals import build_qb_plan_tags, download_plan_hash, snapshot_hash
from app.services.automation import add_decision_once, build_automation_decision
from app.services.automation_policy import get_current_policy, publish_policy_revision
from app.services.executions import create_execution_intent, execute_approved_plan
from app.services.preflight import preflight_policy_fingerprint
from app.workers import download_executor as download_executor_module
from app.workers.download_executor import (
    DownloadExecutor,
    DownloadMonitor,
)
from app.workers.download_monitor_main import build_qb_monitor

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


def approved_candidate(info_hash: str, *, site_id: str = "avistaz") -> TorrentCandidate:
    return TorrentCandidate(
        site_id=site_id,
        torrent_id=TORRENT_ID,
        release_title=RELEASE_TITLE,
        details_ref=f"{site_id}:details:approved-fixed-ref",
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
    state: str = "pausedDL",
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
        fetch_error: AppError | None = None,
        manifest_id: str = "avistaz",
        manifest_enabled: bool = True,
        fetch_enabled: bool = True,
        adapter_type: str = "pt_site",
        search_capabilities: dict[str, bool] | None = None,
    ) -> None:
        self.candidates = candidates
        self.payload = payload
        self.search_error = search_error
        self.fetch_error = fetch_error
        self.manifest_id = manifest_id
        self.manifest_enabled = manifest_enabled
        self.fetch_enabled = fetch_enabled
        self.adapter_type = adapter_type
        self.search_capabilities = search_capabilities or {
            "tmdb_search": True,
            "imdb_search": True,
            "text_search": True,
        }
        self.search_calls: list[TorrentSearchRequest] = []
        self.fetch_calls: list[str] = []
        self.closed = False
        self.before_request: Callable[[], Awaitable[None]] | None = None

    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            id=self.manifest_id,
            name="AvistaZ fixture",
            adapter_type=self.adapter_type,
            version="test",
            enabled=self.manifest_enabled,
            mode="FIXTURE_EXECUTION",
            description="Offline execution fixture",
            capabilities={
                "fetch_torrent_enabled": self.fetch_enabled,
                **self.search_capabilities,
            },
        )

    def set_before_request_guard(
        self, guard: Callable[[], Awaitable[None]] | None
    ) -> None:
        self.before_request = guard

    async def search(self, request: TorrentSearchRequest) -> list[TorrentCandidate]:
        if self.before_request is not None:
            await self.before_request()
        self.search_calls.append(request)
        if self.search_error is not None:
            raise self.search_error
        return self.candidates

    async def fetch_torrent(self, torrent_id: str) -> bytes:
        if self.before_request is not None:
            await self.before_request()
        self.fetch_calls.append(torrent_id)
        if self.fetch_error is not None:
            raise self.fetch_error
        return self.payload

    async def aclose(self) -> None:
        self.closed = True


def execution_registry(
    factory: Callable[[], PtExecutionAdapter],
    *,
    site_id: str = "avistaz",
    fetchable: bool = True,
    search_modes: tuple[PtSearchMode, ...] = (
        PtSearchMode.TMDB_ID,
        PtSearchMode.TEXT,
    ),
    media_types: tuple[MediaType, ...] = (MediaType.MOVIE, MediaType.TV),
) -> PtExecutionRegistry:
    catalog = PtSiteCatalog(
        (
            PtSiteDeclaration(
                site_id=site_id,
                display_name="Execution fixture",
                description="Offline execution registry fixture",
                search_modes=search_modes,
                media_types=media_types,
                search_enabled=True,
                runtime_ready=True,
                manual_only=False,
                torrent_fetch_enabled=fetchable,
            ),
        ),
        default_site_id=site_id,
    )
    registry = PtExecutionRegistry(catalog)
    registry.register(site_id, factory)
    return registry


class FakeQb:
    def __init__(
        self,
        list_results: list[list[QbTorrent]],
        *,
        add_result: QbAddResult | None = None,
        add_error: AppError | None = None,
        category_error: AppError | None = None,
        create_category: bool = False,
        before_add_request: Callable[[], Awaitable[None]] | None = None,
        before_write_guard: Callable[[], Awaitable[None]] | None = None,
        before_list_request: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.list_results = list_results
        self.add_result = add_result
        self.add_error = add_error
        self.category_error = category_error
        self.create_category = create_category
        self.before_add_request = before_add_request
        self.before_write_guard = before_write_guard
        self.before_list_request = before_list_request
        self.calls: list[str] = []
        self.hash_queries: list[tuple[str, ...]] = []
        self.added_payloads: list[bytes] = []
        self.added_tags: list[tuple[str, ...]] = []
        self.category_write_guard_calls = 0
        self.write_guard_calls = 0
        self.before_request: Callable[[], Awaitable[None]] | None = None

    def set_before_request_guard(
        self, guard: Callable[[], Awaitable[None]] | None
    ) -> None:
        self.before_request = guard

    async def _guard_request(self) -> None:
        if self.before_request is not None:
            await self.before_request()

    async def authenticate(self) -> None:
        await self._guard_request()
        self.calls.append("authenticate")

    async def find_torrents_by_hashes(
        self, hashes: Collection[str]
    ) -> list[QbTorrent]:
        if self.before_list_request is not None:
            await self.before_list_request()
        await self._guard_request()
        self.calls.append("find_torrents_by_hashes")
        self.hash_queries.append(tuple(hashes))
        index = min(
            self.calls.count("find_torrents_by_hashes") - 1,
            len(self.list_results) - 1,
        )
        return self.list_results[index]

    async def ensure_category(
        self,
        category: str,
        save_path: str,
        *,
        write_guard: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        del category, save_path
        if self.create_category or self.category_error is not None:
            if write_guard is not None:
                self.category_write_guard_calls += 1
                await write_guard()
            self.calls.append("create_category")
            if self.category_error is not None:
                raise self.category_error

    async def add_torrent(
        self,
        torrent: bytes,
        *,
        expected_info_hash: str,
        save_path: str,
        category: str,
        tags: tuple[str, ...] = (),
        start_immediately: bool = True,
        category_prepared: bool = False,
        category_write_guard: Callable[[], Awaitable[None]] | None = None,
        write_guard: Callable[[], Awaitable[None]] | None = None,
    ) -> QbAddResult:
        del expected_info_hash, start_immediately
        if not category_prepared:
            await self.ensure_category(
                category,
                save_path,
                write_guard=category_write_guard or write_guard,
            )
        if self.before_add_request is not None:
            await self.before_add_request()
        await self._guard_request()
        if self.before_write_guard is not None:
            await self.before_write_guard()
        if write_guard is not None:
            self.write_guard_calls += 1
            await write_guard()
        self.calls.append("add_torrent")
        self.added_payloads.append(torrent)
        self.added_tags.append(tags)
        if self.add_error is not None:
            raise self.add_error
        if self.add_result is None:
            raise AssertionError("add result was not configured")
        return self.add_result

    async def aclose(self) -> None:
        self.calls.append("close")


class CrashingQb(FakeQb):
    async def add_torrent(
        self,
        torrent: bytes,
        *,
        expected_info_hash: str,
        save_path: str,
        category: str,
        tags: tuple[str, ...] = (),
        start_immediately: bool = True,
        category_prepared: bool = False,
        category_write_guard: Callable[[], Awaitable[None]] | None = None,
        write_guard: Callable[[], Awaitable[None]] | None = None,
    ) -> NoReturn:
        del (
            torrent,
            expected_info_hash,
            save_path,
            category,
            tags,
            start_immediately,
            category_prepared,
            category_write_guard,
            write_guard,
        )
        self.calls.append("add_torrent")
        raise KeyboardInterrupt("simulated process crash")


def automation_policy_request(
    base_revision_no: int,
    *,
    execution_mode: AutomationMode,
) -> AutomationPolicyRevisionCreateRequest:
    return AutomationPolicyRevisionCreateRequest(
        base_revision_no=base_revision_no,
        identity_mode=AutomationMode.MANUAL,
        torrent_selection_mode=AutomationMode.MANUAL,
        approval_mode=AutomationMode.MANUAL,
        execution_mode=execution_mode,
        acknowledges_hnr=True,
        acknowledges_seeding=True,
        acknowledges_plan_only=True,
        acknowledges_add_paused_only=True,
    )


async def disable_automatic_execution(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        head, _ = await get_current_policy(session)
        await publish_policy_revision(
            session,
            automation_policy_request(
                head.version,
                execution_mode=AutomationMode.MANUAL,
            ),
            actor="policy-test",
        )
        await session.commit()


async def seed_pending_execution(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    info_hash: str,
    *,
    automatic: bool = False,
    site_id: str = "avistaz",
    media_type: MediaType = MediaType.MOVIE,
) -> str:
    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=1)
    async with session_factory() as session:
        automation_revision = None
        if automatic:
            head, _ = await get_current_policy(session)
            await session.commit()
            _, automation_revision = await publish_policy_revision(
                session,
                automation_policy_request(
                    head.version,
                    execution_mode=AutomationMode.AUTO_IF_ELIGIBLE,
                ),
                actor="policy-test",
            )
        media = MediaItem(
            source="nextfind",
            source_item_id=f"executor-media-{info_hash[:8]}",
            media_type=media_type,
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
            site_id=site_id,
            status=WorkflowStatus.TORRENT_REVIEW,
            candidate_count=1,
        )
        session.add(search_run)
        await session.flush()
        candidate = TorrentCandidateRecord(
            search_run_id=search_run.id,
            site_id=site_id,
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
            site_id=site_id,
            torrent_id=TORRENT_ID,
            torrent_ref=f"{site_id}:details:approved-fixed-ref",
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
            policy_fingerprint=preflight_policy_fingerprint(settings),
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
            site_id=site_id,
            torrent_ref=f"{site_id}:details:approved-fixed-ref",
            expected_info_hash=info_hash,
            release_title=RELEASE_TITLE,
            save_path_ref="movies-root",
            category="movies",
            tags=list(build_qb_plan_tags(settings.qb_plan_tags, media.title)),
            estimated_size_bytes=1024,
            media_destination_plan=destination.model_dump(mode="json"),
            preflight_result=preflight.model_dump(mode="json"),
            warnings=[],
            created_at=now,
        )
        plan.plan_hash = download_plan_hash(plan)
        session.add(plan)
        await session.flush()
        origin = Origin.AUTOMATION if automatic else Origin.MANUAL
        execution_id = str(uuid.uuid4()) if automatic else None
        automation_decision_id = None
        if automation_revision is not None:
            assert execution_id is not None
            decision = build_automation_decision(
                automation_revision,
                stage=AutomationStage.EXECUTION,
                action="CREATE_DOWNLOAD_EXECUTION",
                outcome=DecisionOutcome.ACTION_CREATED,
                media_item_id=media.id,
                approval_request_id=approval.id,
                download_execution_id=execution_id,
                reason_codes=("APPROVED_PLAN_ELIGIBLE",),
                evidence={
                    "approval_request_id": approval.id,
                    "launch_mode": DownloadLaunchMode.ADD_PAUSED.value,
                },
            )
            stored, _ = await add_decision_once(session, decision)
            automation_decision_id = stored.id
        intent, nonce = await create_execution_intent(
            session,
            approval.id,
            ExecutionIntentCreateRequest(launch_mode=DownloadLaunchMode.ADD_PAUSED),
            settings,
            actor="execution-admin",
            origin=origin,
            automation_policy_revision_id=(
                automation_revision.id if automation_revision is not None else None
            ),
            automation_decision_id=automation_decision_id,
        )
        execution, created = await execute_approved_plan(
            session,
            approval.id,
            DownloadExecutionCreateRequest(intent_id=intent.id, nonce=nonce),
            f"executor-idempotency-{info_hash}",
            settings,
            actor="execution-admin",
            origin=origin,
            automation_policy_revision_id=(
                automation_revision.id if automation_revision is not None else None
            ),
            automation_decision_id=automation_decision_id,
            execution_id=execution_id,
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
        execution_registry(forbidden_factory),
        forbidden_factory,
        settings,
    )
    with pytest.raises(AppError) as caught:
        await executor.run_once()

    assert caught.value.error_code == "DOWNLOAD_EXECUTOR_DISABLED"
    assert caught.value.details == {"missing_flags": ["ENABLE_QB_WRITE"]}
    assert calls == 0


@pytest.mark.asyncio
async def test_executor_rejects_preflight_policy_drift_before_external_factories(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload, info_hash = torrent_fixture()
    del payload
    settings = executor_settings()
    execution_id = await seed_pending_execution(session_factory, settings, info_hash)
    pt_factory_calls = 0
    qb_factory_calls = 0

    def forbidden_pt_factory() -> NoReturn:
        nonlocal pt_factory_calls
        pt_factory_calls += 1
        raise AssertionError("PT factory must not run after preflight policy drift")

    def forbidden_qb_factory() -> NoReturn:
        nonlocal qb_factory_calls
        qb_factory_calls += 1
        raise AssertionError("qB factory must not run after preflight policy drift")

    drifted = executor_settings(max_candidate_size_bytes=512)
    executor = DownloadExecutor(
        session_factory,
        "executor-policy-drift",
        execution_registry(forbidden_pt_factory),
        forbidden_qb_factory,
        drifted,
    )

    assert await executor.run_once() is True
    assert pt_factory_calls == 0
    assert qb_factory_calls == 0

    async with session_factory() as session:
        execution = await session.get(DownloadExecution, execution_id)
        assert execution is not None
        assert execution.status == DownloadExecutionStatus.FAILED
        assert execution.error_code == "PREFLIGHT_CONFIG_CHANGED"
        assert (
            await session.scalar(
                select(DownloadExecutionEvent)
                .where(
                    DownloadExecutionEvent.download_execution_id == execution_id,
                    DownloadExecutionEvent.event_type == "VALIDATION_FAILED",
                )
                .limit(1)
            )
            is not None
        )


@pytest.mark.asyncio
async def test_executor_request_guard_rechecks_policy_changed_after_binding_load(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload, info_hash = torrent_fixture()
    settings = executor_settings()
    execution_id = await seed_pending_execution(session_factory, settings, info_hash)
    pt = FakeAvistaZ([approved_candidate(info_hash)], payload)
    qb_factory_calls = 0

    def drifting_pt_factory() -> FakeAvistaZ:
        settings.max_candidate_size_bytes = 512
        return pt

    def forbidden_qb_factory() -> NoReturn:
        nonlocal qb_factory_calls
        qb_factory_calls += 1
        raise AssertionError("qB factory must not run after in-flight policy drift")

    executor = DownloadExecutor(
        session_factory,
        "executor-in-flight-policy-drift",
        execution_registry(drifting_pt_factory),
        forbidden_qb_factory,
        settings,
    )

    assert await executor.run_once() is True
    assert pt.search_calls == []
    assert pt.fetch_calls == []
    assert pt.closed is True
    assert qb_factory_calls == 0

    async with session_factory() as session:
        execution = await session.get(DownloadExecution, execution_id)
        assert execution is not None
        assert execution.status == DownloadExecutionStatus.FAILED
        assert execution.error_code == "PREFLIGHT_CONFIG_CHANGED"


@pytest.mark.asyncio
async def test_execution_registry_blocks_non_fetchable_site_before_factory() -> None:
    factory_calls = 0

    def forbidden_factory() -> NoReturn:
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("search-only site factory must not run")

    registry = execution_registry(
        forbidden_factory,
        site_id="fixture-search-only",
        fetchable=False,
        search_modes=(PtSearchMode.TEXT,),
    )

    with pytest.raises(AppError) as caught:
        await registry.create("fixture-search-only")

    assert caught.value.error_code == "PT_SITE_TORRENT_FETCH_UNSUPPORTED"
    assert factory_calls == 0


@pytest.mark.asyncio
async def test_executor_blocks_search_only_site_before_pt_or_qb_factory(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, info_hash = torrent_fixture()
    settings = executor_settings()
    site_id = "fixture-search-only"
    execution_id = await seed_pending_execution(
        session_factory,
        settings,
        info_hash,
        site_id=site_id,
    )
    pt_factory_calls = 0
    qb_factory_calls = 0

    def forbidden_pt_factory() -> NoReturn:
        nonlocal pt_factory_calls
        pt_factory_calls += 1
        raise AssertionError("search-only site must not create a PT execution adapter")

    def forbidden_qb_factory() -> NoReturn:
        nonlocal qb_factory_calls
        qb_factory_calls += 1
        raise AssertionError("search-only site must not create a qB adapter")

    registry = execution_registry(
        forbidden_pt_factory,
        site_id=site_id,
        fetchable=False,
        search_modes=(PtSearchMode.TEXT,),
    )
    executor = DownloadExecutor(
        session_factory,
        "executor-search-only",
        registry,
        forbidden_qb_factory,
        settings,
    )

    assert await executor.run_once() is True
    assert pt_factory_calls == 0
    assert qb_factory_calls == 0
    async with session_factory() as session:
        execution = await session.get(DownloadExecution, execution_id)
        assert execution is not None
        assert execution.status == DownloadExecutionStatus.FAILED
        assert execution.error_code == "PT_SITE_TORRENT_FETCH_UNSUPPORTED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "manifest_id",
        "fetch_enabled",
        "manifest_enabled",
        "adapter_type",
        "expected_error",
    ),
    [
        (
            "other-site",
            True,
            True,
            "pt_site",
            "PT_SITE_EXECUTION_ADAPTER_ID_MISMATCH",
        ),
        (
            "avistaz",
            False,
            True,
            "pt_site",
            "PT_SITE_EXECUTION_ADAPTER_CAPABILITY_MISMATCH",
        ),
        (
            "avistaz",
            True,
            False,
            "pt_site",
            "PT_SITE_EXECUTION_ADAPTER_CAPABILITY_MISMATCH",
        ),
        (
            "avistaz",
            True,
            True,
            "metadata",
            "PT_SITE_EXECUTION_ADAPTER_CAPABILITY_MISMATCH",
        ),
    ],
)
async def test_execution_registry_rejects_manifest_mismatch_and_closes_adapter(
    manifest_id: str,
    fetch_enabled: bool,
    manifest_enabled: bool,
    adapter_type: str,
    expected_error: str,
) -> None:
    payload, info_hash = torrent_fixture()
    adapter = FakeAvistaZ(
        [approved_candidate(info_hash)],
        payload,
        manifest_id=manifest_id,
        fetch_enabled=fetch_enabled,
        manifest_enabled=manifest_enabled,
        adapter_type=adapter_type,
    )
    registry = execution_registry(lambda: adapter)

    with pytest.raises(AppError) as caught:
        await registry.create("avistaz")

    assert caught.value.error_code == expected_error
    assert adapter.closed is True
    assert adapter.search_calls == []
    assert adapter.fetch_calls == []


@pytest.mark.asyncio
async def test_execution_registry_rejects_declared_search_capability_mismatch() -> None:
    payload, info_hash = torrent_fixture()
    adapter = FakeAvistaZ(
        [approved_candidate(info_hash)],
        payload,
        search_capabilities={
            "tmdb_search": True,
            "imdb_search": True,
            "text_search": False,
        },
    )
    registry = execution_registry(lambda: adapter)

    with pytest.raises(AppError) as caught:
        await registry.create("avistaz")

    assert caught.value.error_code == "PT_SITE_EXECUTION_ADAPTER_CAPABILITY_MISMATCH"
    assert adapter.closed is True
    assert adapter.search_calls == []
    assert adapter.fetch_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("search_modes", "media_types", "media_type", "expected_error"),
    [
        (
            (PtSearchMode.IMDB_ID,),
            (MediaType.MOVIE, MediaType.TV),
            MediaType.MOVIE,
            "PT_SITE_EXECUTION_SEARCH_UNSUPPORTED",
        ),
        (
            (PtSearchMode.TEXT,),
            (MediaType.MOVIE,),
            MediaType.TV,
            "PT_SITE_MEDIA_TYPE_UNSUPPORTED",
        ),
    ],
)
async def test_executor_blocks_unsupported_binding_before_pt_or_qb_factory(
    session_factory: async_sessionmaker[AsyncSession],
    search_modes: tuple[PtSearchMode, ...],
    media_types: tuple[MediaType, ...],
    media_type: MediaType,
    expected_error: str,
) -> None:
    _, info_hash = torrent_fixture()
    settings = executor_settings()
    site_id = "fixture-executable"
    execution_id = await seed_pending_execution(
        session_factory,
        settings,
        info_hash,
        site_id=site_id,
        media_type=media_type,
    )
    pt_factory_calls = 0
    qb_factory_calls = 0

    def forbidden_pt_factory() -> NoReturn:
        nonlocal pt_factory_calls
        pt_factory_calls += 1
        raise AssertionError("unsupported binding must not create a PT adapter")

    def forbidden_qb_factory() -> NoReturn:
        nonlocal qb_factory_calls
        qb_factory_calls += 1
        raise AssertionError("unsupported binding must not create a qB adapter")

    registry = execution_registry(
        forbidden_pt_factory,
        site_id=site_id,
        search_modes=search_modes,
        media_types=media_types,
    )
    executor = DownloadExecutor(
        session_factory,
        "executor-unsupported-binding",
        registry,
        forbidden_qb_factory,
        settings,
    )

    assert await executor.run_once() is True
    assert pt_factory_calls == 0
    assert qb_factory_calls == 0
    async with session_factory() as session:
        execution = await session.get(DownloadExecution, execution_id)
        assert execution is not None
        assert execution.status == DownloadExecutionStatus.FAILED
        assert execution.error_code == expected_error


@pytest.mark.asyncio
async def test_executor_unknown_plan_site_never_falls_back_or_creates_qb(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload, info_hash = torrent_fixture()
    settings = executor_settings()
    site_id = "fixture-executable"
    execution_id = await seed_pending_execution(
        session_factory, settings, info_hash, site_id=site_id
    )
    factory_calls = 0

    def forbidden_factory() -> FakeAvistaZ:
        nonlocal factory_calls
        factory_calls += 1
        return FakeAvistaZ([approved_candidate(info_hash)], payload)

    def forbidden_qb_factory() -> NoReturn:
        raise AssertionError("qB factory must not run")

    registry = execution_registry(forbidden_factory)
    executor = DownloadExecutor(
        session_factory,
        "executor-test",
        registry,
        forbidden_qb_factory,
        settings,
    )

    assert await executor.run_once() is True
    assert factory_calls == 0
    async with session_factory() as session:
        execution = await session.get(DownloadExecution, execution_id)
        assert execution is not None
        assert execution.status == DownloadExecutionStatus.FAILED
        assert execution.error_code == "PT_SITE_NOT_REGISTERED"


@pytest.mark.asyncio
async def test_executor_routes_fixture_site_without_avistaz_fallback(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload, info_hash = torrent_fixture()
    settings = executor_settings()
    site_id = "fixture-executable"
    execution_id = await seed_pending_execution(
        session_factory, settings, info_hash, site_id=site_id
    )
    pt_site = FakeAvistaZ(
        [approved_candidate(info_hash, site_id=site_id)],
        payload,
        manifest_id=site_id,
    )
    observed = qb_observation(info_hash)
    qb = FakeQb(
        [[], [observed]],
        add_result=QbAddResult(info_hash=info_hash, outcome="SUBMITTED"),
    )
    registry = execution_registry(
        lambda: pt_site,
        site_id=site_id,
        search_modes=(PtSearchMode.TEXT,),
    )
    executor = DownloadExecutor(
        session_factory,
        "executor-test",
        registry,
        lambda: qb,
        settings,
    )

    assert await executor.run_once() is True
    assert len(pt_site.search_calls) == 1
    assert pt_site.search_calls[0].tmdb is None
    assert pt_site.search_calls[0].imdb is None
    assert pt_site.search_calls[0].search == RELEASE_TITLE
    assert pt_site.fetch_calls == [TORRENT_ID]
    assert qb.added_payloads == [payload]
    async with session_factory() as session:
        execution = await session.get(DownloadExecution, execution_id)
        assert execution is not None
        assert execution.status == DownloadExecutionStatus.SUBMITTED


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
        session_factory,
        "executor-test",
        execution_registry(lambda: avistaz),
        lambda: qb,
        settings,
    )

    assert await executor.run_once() is True

    assert len(avistaz.search_calls) == 1
    assert avistaz.search_calls[0].tmdb == 424242
    assert avistaz.fetch_calls == [TORRENT_ID]
    assert avistaz.before_request is not None
    assert qb.before_request is not None
    assert qb.calls == [
        "authenticate",
        "find_torrents_by_hashes",
        "add_torrent",
        "find_torrents_by_hashes",
        "close",
    ]
    assert qb.hash_queries == [(info_hash,), (info_hash,)]
    assert qb.added_payloads == [payload]
    assert qb.added_tags == [("unin-plan", "Execution Movie")]
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
        assert job.status == DownloadJobStatus.PAUSED
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
        session_factory,
        "executor-test",
        execution_registry(lambda: avistaz),
        lambda: qb,
        settings,
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
        session_factory,
        "executor-test",
        execution_registry(lambda: avistaz),
        qb_factory,
        settings,
    )

    assert await executor.run_once() is True

    assert len(avistaz.search_calls) == 2
    assert avistaz.search_calls[0].tmdb == 424242
    assert avistaz.search_calls[1].search == RELEASE_TITLE
    assert avistaz.fetch_calls == []
    assert qb_factory_calls == 0
    async with session_factory() as session:
        execution = await session.get(DownloadExecution, execution_id)
        assert execution is not None
        assert execution.status == DownloadExecutionStatus.FAILED
        assert execution.error_code == "AVISTAZ_TORRENT_ID_NOT_UNIQUE"
        assert execution.actual_info_hash is None


@pytest.mark.asyncio
async def test_category_creation_failure_uses_bounded_retry_before_torrent_add(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload, info_hash = torrent_fixture()
    settings = executor_settings()
    execution_id = await seed_pending_execution(session_factory, settings, info_hash)
    first_pt = FakeAvistaZ([approved_candidate(info_hash)], payload)
    second_pt = FakeAvistaZ([approved_candidate(info_hash)], payload)
    pt_adapters = iter((first_pt, second_pt))
    first_qb = FakeQb(
        [[]],
        category_error=AppError(
            "QB_CATEGORY_CREATE_FAILED",
            "category create failed",
            status_code=502,
            retryable=True,
            details={"category": "movies"},
        ),
    )
    observed = qb_observation(info_hash)
    second_qb = FakeQb(
        [[], [observed]],
        add_result=QbAddResult(info_hash=info_hash, outcome="SUBMITTED"),
    )
    qb_adapters = iter((first_qb, second_qb))
    executor = DownloadExecutor(
        session_factory,
        "executor-test",
        execution_registry(lambda: next(pt_adapters)),
        lambda: next(qb_adapters),
        settings,
    )

    assert await executor.run_once() is True

    assert first_qb.category_write_guard_calls == 1
    assert first_qb.write_guard_calls == 0
    assert first_qb.added_payloads == []
    assert first_qb.calls == [
        "authenticate",
        "find_torrents_by_hashes",
        "create_category",
        "close",
    ]
    async with session_factory() as session:
        execution = await session.get(DownloadExecution, execution_id)
        approval = (
            await session.get(ApprovalRequest, execution.approval_id)
            if execution is not None
            else None
        )
        retry_event = await session.scalar(
            select(DownloadExecutionEvent).where(
                DownloadExecutionEvent.download_execution_id == execution_id,
                DownloadExecutionEvent.event_type == "VALIDATION_RETRY_SCHEDULED",
            )
        )
        assert execution is not None
        assert execution.status == DownloadExecutionStatus.RETRY_WAIT
        assert execution.attempts == 1
        assert execution.next_retry_at is not None
        assert execution.actual_info_hash is None
        assert execution.actual_info_hash_v1 is None
        assert execution.actual_info_hash_v2 is None
        assert execution.lease_token is None
        assert approval is not None and approval.status == ApprovalStatus.APPROVED
        assert retry_event is not None
        assert retry_event.sanitized_details == {
            "error_code": "QB_CATEGORY_CREATE_FAILED",
            "attempt": 1,
            "automatic_retry_allowed": True,
            "external_write_performed": False,
            "category_write_may_have_occurred": True,
        }
        execution.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    assert await executor.run_once() is True

    assert second_qb.category_write_guard_calls == 0
    assert second_qb.write_guard_calls == 1
    assert second_qb.added_payloads == [payload]
    async with session_factory() as session:
        execution = await session.get(DownloadExecution, execution_id)
        approval = (
            await session.get(ApprovalRequest, execution.approval_id)
            if execution is not None
            else None
        )
        assert execution is not None
        assert execution.status == DownloadExecutionStatus.SUBMITTED
        assert execution.attempts == 2
        assert approval is not None and approval.status == ApprovalStatus.CONSUMED


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
        create_category=True,
        add_error=AppError(
            "QB_ADD_OUTCOME_UNKNOWN",
            "qB add result unknown",
            status_code=502,
            details={"external_write_may_have_occurred": True},
        ),
    )
    executor = DownloadExecutor(
        session_factory,
        "executor-test",
        execution_registry(lambda: avistaz),
        lambda: qb,
        settings,
    )

    assert await executor.run_once() is True
    assert await executor.run_once() is False

    assert qb.category_write_guard_calls == 1
    assert qb.write_guard_calls == 1
    assert qb.calls.count("create_category") == 1
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
async def test_automatic_policy_change_before_qb_post_cancels_without_write(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload, info_hash = torrent_fixture()
    settings = executor_settings(
        enable_automation_engine=True,
        enable_avistaz_live_search=True,
    )
    execution_id = await seed_pending_execution(
        session_factory,
        settings,
        info_hash,
        automatic=True,
    )
    avistaz = FakeAvistaZ([approved_candidate(info_hash)], payload)
    qb = FakeQb(
        [[]],
        add_result=QbAddResult(info_hash=info_hash, outcome="SUBMITTED"),
        before_add_request=lambda: disable_automatic_execution(session_factory),
    )
    executor = DownloadExecutor(
        session_factory,
        "executor-test",
        execution_registry(lambda: avistaz),
        lambda: qb,
        settings,
    )

    assert await executor.run_once() is True

    assert qb.calls == ["authenticate", "find_torrents_by_hashes", "close"]
    assert qb.write_guard_calls == 0
    assert qb.added_payloads == []
    async with session_factory() as session:
        execution = await session.get(DownloadExecution, execution_id)
        approval = (
            await session.get(ApprovalRequest, execution.approval_id)
            if execution is not None
            else None
        )
        jobs = list(
            (
                await session.scalars(
                    select(DownloadJob).where(DownloadJob.execution_id == execution_id)
                )
            ).all()
        )
        assert execution is not None
        assert execution.status == DownloadExecutionStatus.CANCELLED
        assert execution.error_code == "AUTOMATION_POLICY_REVISION_CHANGED"
        assert execution.next_retry_at is None
        assert execution.lease_token is None
        assert approval is not None and approval.status == ApprovalStatus.REVOKED
        assert jobs == []


@pytest.mark.asyncio
async def test_write_guard_blocks_policy_change_after_global_request_guard(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload, info_hash = torrent_fixture()
    settings = executor_settings(
        enable_automation_engine=True,
        enable_avistaz_live_search=True,
    )
    execution_id = await seed_pending_execution(
        session_factory,
        settings,
        info_hash,
        automatic=True,
    )
    avistaz = FakeAvistaZ([approved_candidate(info_hash)], payload)
    qb = FakeQb(
        [[]],
        add_result=QbAddResult(info_hash=info_hash, outcome="SUBMITTED"),
        before_write_guard=lambda: disable_automatic_execution(session_factory),
    )
    executor = DownloadExecutor(
        session_factory,
        "executor-test",
        execution_registry(lambda: avistaz),
        lambda: qb,
        settings,
    )

    assert await executor.run_once() is True

    assert qb.calls == ["authenticate", "find_torrents_by_hashes", "close"]
    assert qb.write_guard_calls == 1
    assert qb.added_payloads == []
    async with session_factory() as session:
        execution = await session.get(DownloadExecution, execution_id)
        approval = (
            await session.get(ApprovalRequest, execution.approval_id)
            if execution is not None
            else None
        )
        assert execution is not None
        assert execution.status == DownloadExecutionStatus.CANCELLED
        assert execution.error_code == "AUTOMATION_POLICY_REVISION_CHANGED"
        assert execution.next_retry_at is None
        assert approval is not None and approval.status == ApprovalStatus.REVOKED


@pytest.mark.asyncio
async def test_automatic_policy_change_after_qb_add_requires_manual_reconciliation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload, info_hash = torrent_fixture()
    settings = executor_settings(
        enable_automation_engine=True,
        enable_avistaz_live_search=True,
    )
    execution_id = await seed_pending_execution(
        session_factory,
        settings,
        info_hash,
        automatic=True,
    )
    list_attempts = 0

    async def change_policy_before_post_add_observation() -> None:
        nonlocal list_attempts
        list_attempts += 1
        if list_attempts == 2:
            await disable_automatic_execution(session_factory)

    avistaz = FakeAvistaZ([approved_candidate(info_hash)], payload)
    qb = FakeQb(
        [[], [qb_observation(info_hash)]],
        add_result=QbAddResult(info_hash=info_hash, outcome="SUBMITTED"),
        before_list_request=change_policy_before_post_add_observation,
    )
    executor = DownloadExecutor(
        session_factory,
        "executor-test",
        execution_registry(lambda: avistaz),
        lambda: qb,
        settings,
    )

    assert await executor.run_once() is True

    assert qb.calls == [
        "authenticate",
        "find_torrents_by_hashes",
        "add_torrent",
        "close",
    ]
    assert list_attempts == 2
    assert qb.write_guard_calls == 1
    assert qb.added_payloads == [payload]
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
        assert execution.error_code == "AUTOMATION_POLICY_CHANGED_AFTER_WRITE"
        assert execution.next_retry_at is None
        assert execution.lease_token is None
        assert jobs == []


@pytest.mark.asyncio
async def test_policy_change_between_final_fence_and_finalize_is_outcome_unknown(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, info_hash = torrent_fixture()
    settings = executor_settings(
        enable_automation_engine=True,
        enable_avistaz_live_search=True,
    )
    execution_id = await seed_pending_execution(
        session_factory,
        settings,
        info_hash,
        automatic=True,
    )
    avistaz = FakeAvistaZ([approved_candidate(info_hash)], payload)
    observed = qb_observation(info_hash)
    qb = FakeQb(
        [[], [observed]],
        add_result=QbAddResult(info_hash=info_hash, outcome="SUBMITTED"),
    )
    real_finalize = download_executor_module.finalize_download_submission

    async def change_policy_then_finalize(*args: object, **kwargs: object) -> object:
        await disable_automatic_execution(session_factory)
        return await real_finalize(*args, **kwargs)

    monkeypatch.setattr(
        download_executor_module,
        "finalize_download_submission",
        change_policy_then_finalize,
    )
    executor = DownloadExecutor(
        session_factory,
        "executor-test",
        execution_registry(lambda: avistaz),
        lambda: qb,
        settings,
    )

    assert await executor.run_once() is True

    assert qb.calls == [
        "authenticate",
        "find_torrents_by_hashes",
        "add_torrent",
        "find_torrents_by_hashes",
        "close",
    ]
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
        assert execution.error_code == "AUTOMATION_POLICY_CHANGED_AFTER_WRITE"
        assert execution.next_retry_at is None
        assert jobs == []


@pytest.mark.asyncio
async def test_automatic_execution_rejects_candidate_without_tmdb_binding(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload, info_hash = torrent_fixture()
    settings = executor_settings(
        enable_automation_engine=True,
        enable_avistaz_live_search=True,
    )
    execution_id = await seed_pending_execution(
        session_factory,
        settings,
        info_hash,
        automatic=True,
    )
    candidate = approved_candidate(info_hash).model_copy(update={"tmdb_id": None})
    avistaz = FakeAvistaZ([candidate], payload)
    qb_factory_calls = 0

    def qb_factory() -> NoReturn:
        nonlocal qb_factory_calls
        qb_factory_calls += 1
        raise AssertionError("qB must not be created for an unbound TMDB candidate")

    executor = DownloadExecutor(
        session_factory,
        "executor-test",
        execution_registry(lambda: avistaz),
        qb_factory,
        settings,
    )

    assert await executor.run_once() is True

    assert avistaz.fetch_calls == []
    assert qb_factory_calls == 0
    async with session_factory() as session:
        execution = await session.get(DownloadExecution, execution_id)
        assert execution is not None
        assert execution.status == DownloadExecutionStatus.FAILED
        assert execution.error_code == "AVISTAZ_CANDIDATE_BINDING_DRIFT"
        assert execution.actual_info_hash is None


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
        session_factory,
        "executor-test",
        execution_registry(lambda: avistaz),
        qb_factory,
        settings,
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
async def test_avistaz_fetch_403_records_safe_status_and_scheduled_retry(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload, info_hash = torrent_fixture()
    settings = executor_settings()
    execution_id = await seed_pending_execution(session_factory, settings, info_hash)
    avistaz = FakeAvistaZ(
        [approved_candidate(info_hash)],
        payload,
        fetch_error=AppError(
            "AVISTAZ_TORRENT_FETCH_FORBIDDEN",
            f"fetch failed at {SECRET_URL}",
            status_code=502,
            retryable=True,
            details={
                "upstream_status_code": 403,
                "request_url": SECRET_URL,
                "response_body": "private tracker response",
                "headers": {"Authorization": "Bearer secret-token"},
                "passkey": "secret-passkey",
            },
        ),
    )
    qb_factory_calls = 0

    def qb_factory() -> NoReturn:
        nonlocal qb_factory_calls
        qb_factory_calls += 1
        raise AssertionError("qB must not be created before torrent validation")

    executor = DownloadExecutor(
        session_factory,
        "executor-test",
        execution_registry(lambda: avistaz),
        qb_factory,
        settings,
    )

    assert await executor.run_once() is True

    assert qb_factory_calls == 0
    async with session_factory() as session:
        execution = await session.get(DownloadExecution, execution_id)
        event = await session.scalar(
            select(DownloadExecutionEvent).where(
                DownloadExecutionEvent.download_execution_id == execution_id,
                DownloadExecutionEvent.event_type == "VALIDATION_RETRY_SCHEDULED",
            )
        )
        assert execution is not None
        assert event is not None
        assert execution.status == DownloadExecutionStatus.RETRY_WAIT
        assert execution.next_retry_at is not None
        assert execution.error_message == (
            "AvistaZ 返回 HTTP 403，已安排自动重试（第 1/3 次）"
        )
        assert event.sanitized_details == {
            "error_code": "AVISTAZ_TORRENT_FETCH_FORBIDDEN",
            "attempt": 1,
            "automatic_retry_allowed": True,
            "external_write_performed": False,
            "upstream_status_code": 403,
        }
        serialized = repr([execution.error_message, event.sanitized_details])
        assert "secret-pid" not in serialized
        assert "secret-passkey" not in serialized
        assert "private tracker response" not in serialized
        assert "Authorization" not in serialized
        assert "request_url" not in serialized


@pytest.mark.asyncio
async def test_avistaz_fetch_403_reports_exhausted_retry_budget(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload, info_hash = torrent_fixture()
    settings = executor_settings()
    execution_id = await seed_pending_execution(session_factory, settings, info_hash)
    async with session_factory() as session:
        execution = await session.get(DownloadExecution, execution_id)
        assert execution is not None
        execution.attempts = execution.max_attempts - 1
        await session.commit()
    avistaz = FakeAvistaZ(
        [approved_candidate(info_hash)],
        payload,
        fetch_error=AppError(
            "AVISTAZ_TORRENT_FETCH_FORBIDDEN",
            "torrent fetch forbidden",
            status_code=502,
            retryable=True,
            details={"upstream_status_code": 403},
        ),
    )
    executor = DownloadExecutor(
        session_factory,
        "executor-test",
        execution_registry(lambda: avistaz),
        lambda: (_ for _ in ()).throw(
            AssertionError("qB must not be created before torrent validation")
        ),
        settings,
    )

    assert await executor.run_once() is True

    async with session_factory() as session:
        execution = await session.get(DownloadExecution, execution_id)
        event = await session.scalar(
            select(DownloadExecutionEvent).where(
                DownloadExecutionEvent.download_execution_id == execution_id,
                DownloadExecutionEvent.event_type == "VALIDATION_FAILED",
            )
        )
        assert execution is not None
        assert event is not None
        assert execution.status == DownloadExecutionStatus.FAILED
        assert execution.attempts == execution.max_attempts == 3
        assert execution.next_retry_at is None
        assert execution.error_message == (
            "AvistaZ 返回 HTTP 403，未安排自动重试或重试次数已耗尽"
            "（第 3/3 次）"
        )
        assert event.sanitized_details == {
            "error_code": "AVISTAZ_TORRENT_FETCH_FORBIDDEN",
            "attempt": 3,
            "automatic_retry_allowed": False,
            "external_write_performed": False,
            "upstream_status_code": 403,
        }


@pytest.mark.asyncio
async def test_avistaz_fetch_403_retries_with_fresh_adapter_and_submits(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload, info_hash = torrent_fixture()
    settings = executor_settings()
    execution_id = await seed_pending_execution(session_factory, settings, info_hash)
    first_adapter = FakeAvistaZ(
        [approved_candidate(info_hash)],
        payload,
        fetch_error=AppError(
            "AVISTAZ_TORRENT_FETCH_FORBIDDEN",
            "torrent fetch forbidden",
            status_code=502,
            retryable=True,
            details={"upstream_status_code": 403},
        ),
    )
    second_adapter = FakeAvistaZ([approved_candidate(info_hash)], payload)
    adapters = iter((first_adapter, second_adapter))
    observed = qb_observation(info_hash)
    qb = FakeQb(
        [[], [observed]],
        add_result=QbAddResult(info_hash=info_hash, outcome="SUBMITTED"),
    )
    executor = DownloadExecutor(
        session_factory,
        "executor-test",
        execution_registry(lambda: next(adapters)),
        lambda: qb,
        settings,
    )

    assert await executor.run_once() is True
    async with session_factory() as session:
        execution = await session.get(DownloadExecution, execution_id)
        assert execution is not None
        assert execution.status == DownloadExecutionStatus.RETRY_WAIT
        assert execution.attempts == 1
        execution.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    assert await executor.run_once() is True

    assert first_adapter.search_calls
    assert first_adapter.fetch_calls == [TORRENT_ID]
    assert first_adapter.closed is True
    assert second_adapter.search_calls
    assert second_adapter.fetch_calls == [TORRENT_ID]
    assert second_adapter.closed is True
    assert qb.calls == [
        "authenticate",
        "find_torrents_by_hashes",
        "add_torrent",
        "find_torrents_by_hashes",
        "close",
    ]
    async with session_factory() as session:
        execution = await session.get(DownloadExecution, execution_id)
        job = await session.scalar(
            select(DownloadJob).where(DownloadJob.execution_id == execution_id)
        )
        event_types = list(
            (
                await session.scalars(
                    select(DownloadExecutionEvent.event_type).where(
                        DownloadExecutionEvent.download_execution_id == execution_id
                    )
                )
            ).all()
        )
        assert execution is not None
        assert execution.status == DownloadExecutionStatus.SUBMITTED
        assert execution.attempts == 2
        assert execution.error_code is None
        assert execution.error_message is None
        assert job is not None
        assert event_types.count("VALIDATION_CLAIMED") == 2
        assert "VALIDATION_RETRY_SCHEDULED" in event_types


@pytest.mark.parametrize("upstream_status", [400, 599])
def test_safe_upstream_status_accepts_http_error_boundaries(
    upstream_status: int,
) -> None:
    error = AppError(
        "UPSTREAM_FAILURE",
        "upstream failed",
        details={"upstream_status_code": upstream_status},
    )

    assert DownloadExecutor._safe_upstream_status_code(error) == upstream_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_status",
    [True, "403", 403.0, 399, 600, {}, []],
    ids=["bool", "string", "float", "below-range", "above-range", "dict", "list"],
)
async def test_invalid_upstream_status_is_not_persisted_or_displayed(
    session_factory: async_sessionmaker[AsyncSession],
    invalid_status: object,
) -> None:
    payload, info_hash = torrent_fixture()
    settings = executor_settings()
    execution_id = await seed_pending_execution(session_factory, settings, info_hash)
    avistaz = FakeAvistaZ(
        [approved_candidate(info_hash)],
        payload,
        fetch_error=AppError(
            "AVISTAZ_TORRENT_FETCH_FORBIDDEN",
            f"fetch failed at {SECRET_URL}",
            status_code=502,
            retryable=True,
            details={
                "upstream_status_code": invalid_status,
                "response_body": "private tracker response",
            },
        ),
    )
    executor = DownloadExecutor(
        session_factory,
        "executor-test",
        execution_registry(lambda: avistaz),
        lambda: (_ for _ in ()).throw(
            AssertionError("qB must not be created before torrent validation")
        ),
        settings,
    )

    assert await executor.run_once() is True

    async with session_factory() as session:
        execution = await session.get(DownloadExecution, execution_id)
        event = await session.scalar(
            select(DownloadExecutionEvent).where(
                DownloadExecutionEvent.download_execution_id == execution_id,
                DownloadExecutionEvent.event_type == "VALIDATION_RETRY_SCHEDULED",
            )
        )
        assert execution is not None
        assert event is not None
        assert execution.error_message == "下载执行在写入前失败，请根据错误代码处理"
        assert event.sanitized_details == {
            "error_code": "AVISTAZ_TORRENT_FETCH_FORBIDDEN",
            "attempt": 1,
            "automatic_retry_allowed": True,
            "external_write_performed": False,
        }
        serialized = repr([execution.error_message, event.sanitized_details])
        assert "secret-pid" not in serialized
        assert "private tracker response" not in serialized


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
        session_factory,
        "executor-test",
        execution_registry(lambda: avistaz),
        lambda: qb,
        settings,
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
        session_factory,
        "executor-test",
        execution_registry(lambda: avistaz),
        lambda: qb,
        settings,
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
        execution_registry(lambda: avistaz),
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
        execution_registry(lambda: avistaz),
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

    assert monitor_qb.calls == [
        "authenticate",
        "find_torrents_by_hashes",
        "close",
    ]
    assert monitor_qb.hash_queries == [(info_hash,)]
    async with session_factory() as session:
        job = await session.scalar(
            select(DownloadJob).where(DownloadJob.execution_id == execution_id)
        )
        assert job is not None
        assert job.status == DownloadJobStatus.SEEDING
        assert job.hnr_status == HnrStatus.UNKNOWN


@pytest.mark.asyncio
async def test_monitor_records_ambiguous_hash_match_as_auditable_error(
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
        execution_registry(lambda: avistaz),
        lambda: executor_qb,
        settings,
    )
    assert await executor.run_once() is True

    duplicate = initial.model_copy(update={"name": "duplicate qB row"})
    monitor_qb = FakeQb([[initial, duplicate]])
    monitor = DownloadMonitor(
        session_factory,
        "monitor-test",
        lambda: monitor_qb,
        settings,
    )
    assert await monitor.run_once() == 1
    assert await monitor.run_once() == 1
    assert monitor_qb.calls == [
        "authenticate",
        "find_torrents_by_hashes",
        "close",
        "authenticate",
        "find_torrents_by_hashes",
        "close",
    ]

    async with session_factory() as session:
        job = await session.scalar(
            select(DownloadJob).where(DownloadJob.execution_id == execution_id)
        )
        assert job is not None
        assert job.status == DownloadJobStatus.ERROR
        assert job.error_code == "QB_INFO_HASH_AMBIGUOUS"
        events = list(
            (
                await session.scalars(
                    select(DownloadJobEvent).where(
                        DownloadJobEvent.download_job_id == job.id,
                        DownloadJobEvent.event_type == "MONITOR_ERROR",
                    )
                )
            ).all()
        )
        assert len(events) == 1
        assert events[0].from_status == DownloadJobStatus.PAUSED.value
        assert events[0].to_status == DownloadJobStatus.ERROR.value
        assert events[0].sanitized_details == {
            "error_code": "QB_INFO_HASH_AMBIGUOUS",
            "match_count": 2,
        }


@pytest.mark.parametrize(
    ("target_save_path", "allowed_save_paths", "expected_error_code"),
    [
        ("", ("/downloads/movies",), "DOWNLOAD_MONITOR_TARGET_NOT_CONFIGURED"),
        (
            "/downloads/movies/incoming",
            (),
            "DOWNLOAD_MONITOR_TARGET_NOT_ALLOWED",
        ),
        (
            "/private/unapproved",
            ("/downloads/movies",),
            "DOWNLOAD_MONITOR_TARGET_NOT_ALLOWED",
        ),
    ],
)
def test_monitor_startup_gate_rejects_invalid_target_before_credentials(
    target_save_path: str,
    allowed_save_paths: tuple[str, ...],
    expected_error_code: str,
) -> None:
    settings = executor_settings(
        qb_target_save_path=target_save_path,
        qb_allowed_save_paths=allowed_save_paths,
        qb_base_url=None,
        qb_username=None,
        qb_password=None,
    )

    with pytest.raises(AppError) as caught:
        build_qb_monitor(settings)

    assert caught.value.error_code == expected_error_code
