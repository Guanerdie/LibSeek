from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.api.routes.approvals as approval_routes
import app.services.approvals as approval_services
from app.adapters.base import ReadOnlyDownloaderAdapter
from app.api.dependencies import (
    get_admin_principal,
    get_operator_principal,
    get_qb_adapter,
    get_viewer_principal,
)
from app.core.auth import Principal
from app.core.config import Settings
from app.db.session import get_session
from app.errors import AppError
from app.main import app
from app.models.entities import (
    ApprovalEvent,
    ApprovalRequest,
    DownloadExecution,
    DownloadPlan,
    ExecutionIntent,
    MediaItem,
    TorrentCandidateRecord,
    TorrentSearchRun,
)
from app.models.enums import (
    ApprovalStatus,
    AuthRole,
    AutomationMode,
    DownloadExecutionStatus,
    DownloadLaunchMode,
    ExecutionIntentStatus,
    IdentityConfidence,
    MediaType,
    MetadataStatus,
    WorkflowStatus,
)
from app.schemas.adapters import AdapterManifest, TorrentCandidate
from app.schemas.automation import AutomationPolicyRevisionCreateRequest
from app.schemas.qbittorrent import QbCategory, QbTorrent, QbTorrentFile
from app.services.automation_policy import get_current_policy, publish_policy_revision

INFO_HASH = "1234567890abcdef1234567890abcdef12345678"


@asynccontextmanager
async def fake_qb_context(
    adapter: ReadOnlyDownloaderAdapter,
) -> AsyncIterator[ReadOnlyDownloaderAdapter]:
    yield adapter


class ApiFakeQb(ReadOnlyDownloaderAdapter):
    def __init__(
        self,
        *,
        duplicate_info_hash: bool = False,
        category_exists: bool = True,
    ) -> None:
        self.calls: list[str] = []
        self.duplicate_info_hash = duplicate_info_hash
        self.category_exists = category_exists

    def manifest(self) -> AdapterManifest:
        raise NotImplementedError

    async def authenticate(self) -> None:
        self.calls.append("authenticate")

    async def get_version(self) -> str:
        self.calls.append("get_version")
        return "v5.0.4"

    async def get_web_api_version(self) -> str:
        self.calls.append("get_web_api_version")
        return "2.11.4"

    async def list_torrents(self) -> list[QbTorrent]:
        self.calls.append("list_torrents")
        return [
            QbTorrent(
                hash=INFO_HASH if self.duplicate_info_hash else "f" * 40,
                name=(
                    "Phase 3 Movie 2026 1080p"
                    if self.duplicate_info_hash
                    else "Existing Seeder"
                ),
                size=5000 if self.duplicate_info_hash else 100,
                progress=1,
                ratio=2,
                state="uploading",
                upspeed=10,
                save_path="/downloads/movies",
            )
        ]

    async def get_torrent_files(self, info_hash: str) -> list[QbTorrentFile]:
        self.calls.append("get_torrent_files")
        del info_hash
        return []

    async def get_categories(self) -> dict[str, QbCategory]:
        self.calls.append("get_categories")
        return (
            {"movies": QbCategory(name="movies", savePath="/downloads/movies")}
            if self.category_exists
            else {}
        )


@pytest.fixture
def phase3_api_client_factory(session_factory: async_sessionmaker[AsyncSession]):
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    principal = Principal(
        username="phase3-api-admin",
        role=AuthRole.ADMIN,
        issued_at=0,
        expires_at=2**31,
        csrf_digest="0" * 64,
    )

    async def override_principal() -> Principal:
        return principal

    app.dependency_overrides[get_viewer_principal] = override_principal
    app.dependency_overrides[get_operator_principal] = override_principal
    app.dependency_overrides[get_admin_principal] = override_principal

    def create() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )

    yield create
    app.dependency_overrides.clear()


async def seed_api_candidate(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    suffix: str = "",
) -> TorrentCandidateRecord:
    now = datetime.now(UTC)
    tmdb_id = 333 + len(suffix)
    media = MediaItem(
        source="nextfind",
        source_item_id=f"phase3-api-media{suffix}",
        media_type=MediaType.MOVIE,
        tmdb_id=tmdb_id,
        title="Phase 3 Movie",
        year=2026,
        identity_confidence=IdentityConfidence.HIGH,
        metadata_status=MetadataStatus.RESOLVED,
        workflow_status=WorkflowStatus.TORRENT_REVIEW,
        discovered_at=now,
        updated_at=now,
    )
    candidate = TorrentCandidate(
        site_id="avistaz",
        torrent_id=f"phase3-candidate{suffix}",
        release_title="Phase 3 Movie 2026 1080p",
        details_ref=f"avistaz:details:phase3-safe{suffix}",
        media_type=MediaType.MOVIE,
        tmdb_id=tmdb_id,
        year=2026,
        resolution="1080p",
        source="WEB-DL",
        subtitles=["Chinese"],
        size_bytes=5000,
        seeders=5,
        hit_and_run=False,
        info_hash=INFO_HASH,
        download_factor=0,
        upload_factor=1,
        match_score=0.95,
        match_reasons=["TMDB_ID_EXACT", "ACTIVE_SEEDERS"],
        warnings=[],
    )
    async with session_factory() as session:
        session.add(media)
        await session.flush()
        run = TorrentSearchRun(
            media_id=media.id,
            status=WorkflowStatus.TORRENT_REVIEW,
            site_id="avistaz",
            candidate_count=1,
        )
        session.add(run)
        await session.flush()
        record = TorrentCandidateRecord(
            search_run_id=run.id,
            site_id="avistaz",
            torrent_id=candidate.torrent_id,
            candidate_snapshot=candidate.model_dump(mode="json"),
            match_score=0.95,
            match_reasons=candidate.match_reasons,
            warnings=[],
        )
        session.add(record)
        await session.commit()
        return record


def api_settings() -> Settings:
    return Settings(
        _env_file=None,
        avistaz_forbidden_qb_versions=("4.3.*",),
        qb_base_url="https://qb.internal.test",
        qb_allowed_hosts=("qb.internal.test",),
        qb_target_category="movies",
        qb_target_save_path="/downloads/movies/incoming",
        qb_allowed_save_paths=("/downloads/movies",),
        qb_save_path_ref="movies-root",
        qb_plan_tags=("unin-plan",),
        max_candidate_size_bytes=10_000,
        approval_default_ttl_minutes=60,
    )


def confirm_download_settings() -> Settings:
    return api_settings().model_copy(
        update={"enable_download_execution_control_plane": True}
    )


@pytest.mark.asyncio
async def test_confirm_download_creates_pending_execution_and_replays_safely(
    session_factory: async_sessionmaker[AsyncSession],
    phase3_api_client_factory,
    monkeypatch,
) -> None:
    record = await seed_api_candidate(session_factory)
    fake = ApiFakeQb(category_exists=False)

    async def override_qb() -> AsyncIterator[ReadOnlyDownloaderAdapter]:
        yield fake

    app.dependency_overrides[get_qb_adapter] = override_qb
    monkeypatch.setattr(
        approval_routes,
        "open_qb_adapter",
        lambda _settings: fake_qb_context(fake),
    )
    monkeypatch.setattr(approval_routes, "get_settings", confirm_download_settings)
    request_body = {
        "expires_in_minutes": 60,
        "acknowledges_hnr": False,
        "acknowledges_seeding": True,
        "acknowledges_plan_only": True,
    }
    headers = {"Idempotency-Key": "confirm-download-api-key-0001"}

    async with phase3_api_client_factory() as client:
        created = await client.post(
            f"/api/candidates/{record.id}/confirm-download",
            json=request_body,
            headers=headers,
        )
        calls_after_creation = list(fake.calls)

        async def replay_gate_must_not_run(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("an idempotent replay must not evaluate stage gates")

        def replay_settings_must_not_load() -> Settings:
            raise AssertionError("an idempotent replay must not evaluate control-plane settings")

        def replay_qb_must_not_open(_settings: Settings) -> object:
            raise AssertionError("an idempotent replay must not open qBittorrent")

        monkeypatch.setattr(
            approval_routes,
            "require_stage_not_disabled",
            replay_gate_must_not_run,
        )
        monkeypatch.setattr(approval_routes, "get_settings", replay_settings_must_not_load)
        monkeypatch.setattr(approval_routes, "open_qb_adapter", replay_qb_must_not_open)
        replayed = await client.post(
            f"/api/candidates/{record.id}/confirm-download",
            json=request_body,
            headers=headers,
        )

    assert created.status_code == 201, created.text
    created_body = created.json()
    assert created_body["outcome"] == "EXECUTION_CREATED"
    assert created_body["approval_created"] is True
    assert created_body["execution_created"] is True
    assert created_body["approval"]["status"] == "APPROVED"
    assert created_body["preflight"]["overall_status"] == "WARNING"
    assert created_body["execution"]["status"] == "PENDING"
    assert created_body["execution"]["launch_mode"] == "START_IMMEDIATELY"
    assert "nonce" not in created.text.casefold()

    assert replayed.status_code == 200, replayed.text
    replayed_body = replayed.json()
    assert replayed_body["outcome"] == "EXECUTION_REPLAYED"
    assert replayed_body["execution"]["id"] == created_body["execution"]["id"]
    assert replayed_body["approval"]["id"] == created_body["approval"]["id"]
    assert fake.calls == calls_after_creation

    async with session_factory() as session:
        plan = await session.scalar(select(DownloadPlan))
        intent = await session.scalar(select(ExecutionIntent))
        execution = await session.scalar(select(DownloadExecution))
    assert plan is not None
    assert intent is not None
    assert intent.status == ExecutionIntentStatus.CONSUMED
    assert execution is not None
    assert execution.status == DownloadExecutionStatus.PENDING
    assert execution.launch_mode == DownloadLaunchMode.START_IMMEDIATELY


@pytest.mark.asyncio
async def test_confirm_download_reuses_blocked_approval_then_succeeds(
    session_factory: async_sessionmaker[AsyncSession],
    phase3_api_client_factory,
    monkeypatch,
) -> None:
    record = await seed_api_candidate(session_factory)
    fake = ApiFakeQb(duplicate_info_hash=True)

    async def override_qb() -> AsyncIterator[ReadOnlyDownloaderAdapter]:
        yield fake

    app.dependency_overrides[get_qb_adapter] = override_qb
    monkeypatch.setattr(
        approval_routes,
        "open_qb_adapter",
        lambda _settings: fake_qb_context(fake),
    )
    monkeypatch.setattr(approval_routes, "get_settings", confirm_download_settings)
    request_body = {
        "expires_in_minutes": 60,
        "acknowledges_hnr": False,
        "acknowledges_seeding": True,
        "acknowledges_plan_only": True,
        "launch_mode": "ADD_PAUSED",
    }
    headers = {"Idempotency-Key": "confirm-download-api-key-0002"}

    async with phase3_api_client_factory() as client:
        blocked = await client.post(
            f"/api/candidates/{record.id}/confirm-download",
            json=request_body,
            headers=headers,
        )
        fake.duplicate_info_hash = False
        retried = await client.post(
            f"/api/candidates/{record.id}/confirm-download",
            json=request_body,
            headers=headers,
        )

    assert blocked.status_code == 200, blocked.text
    blocked_body = blocked.json()
    assert blocked_body["outcome"] == "PREFLIGHT_BLOCKED"
    assert blocked_body["approval"]["status"] == "PENDING"
    assert blocked_body["execution"] is None
    duplicate_check = next(
        check
        for check in blocked_body["preflight"]["checks"]
        if check["code"] == "DUPLICATE_INFO_HASH"
    )
    assert duplicate_check["status"] == "BLOCKED"

    assert retried.status_code == 201, retried.text
    retried_body = retried.json()
    assert retried_body["outcome"] == "EXECUTION_CREATED"
    assert retried_body["approval_created"] is False
    assert retried_body["approval"]["id"] == blocked_body["approval"]["id"]
    assert retried_body["execution"]["status"] == "PENDING"


@pytest.mark.asyncio
async def test_confirm_download_unknown_preflight_creates_no_execution(
    session_factory: async_sessionmaker[AsyncSession],
    phase3_api_client_factory,
    monkeypatch,
) -> None:
    class UnknownVersionQb(ApiFakeQb):
        async def get_version(self) -> str:
            self.calls.append("get_version")
            raise AppError("QB_VERSION_UNAVAILABLE", "version unavailable", status_code=503)

    record = await seed_api_candidate(session_factory)
    fake = UnknownVersionQb()

    async def override_qb() -> AsyncIterator[ReadOnlyDownloaderAdapter]:
        yield fake

    app.dependency_overrides[get_qb_adapter] = override_qb
    monkeypatch.setattr(
        approval_routes,
        "open_qb_adapter",
        lambda _settings: fake_qb_context(fake),
    )
    monkeypatch.setattr(approval_routes, "get_settings", confirm_download_settings)

    async with phase3_api_client_factory() as client:
        response = await client.post(
            f"/api/candidates/{record.id}/confirm-download",
            json={
                "acknowledges_seeding": True,
                "acknowledges_plan_only": True,
            },
            headers={"Idempotency-Key": "confirm-download-api-key-unknown"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "PREFLIGHT_BLOCKED"
    assert body["preflight"]["overall_status"] == "UNKNOWN"
    assert body["approval"]["status"] == "PENDING"
    assert body["execution"] is None
    async with session_factory() as session:
        assert await session.scalar(select(DownloadPlan)) is None
        assert await session.scalar(select(ExecutionIntent)) is None
        assert await session.scalar(select(DownloadExecution)) is None


@pytest.mark.asyncio
async def test_confirm_download_recovers_when_competing_request_commits_first(
    session_factory: async_sessionmaker[AsyncSession],
    phase3_api_client_factory,
    monkeypatch,
) -> None:
    record = await seed_api_candidate(session_factory)
    fake = ApiFakeQb()
    request_body = {
        "acknowledges_seeding": True,
        "acknowledges_plan_only": True,
        "launch_mode": "ADD_PAUSED",
    }
    headers = {"Idempotency-Key": "confirm-download-api-key-race-01"}
    real_evaluate_preflight = approval_routes.evaluate_preflight
    competing_response: httpx.Response | None = None
    injected = False

    async def override_qb() -> AsyncIterator[ReadOnlyDownloaderAdapter]:
        yield fake

    async def let_competing_request_commit(*args, **kwargs):
        nonlocal competing_response, injected
        result = await real_evaluate_preflight(*args, **kwargs)
        if not injected:
            injected = True
            async with phase3_api_client_factory() as competing_client:
                competing_response = await competing_client.post(
                    f"/api/candidates/{record.id}/confirm-download",
                    json=request_body,
                    headers=headers,
                )
        return result

    app.dependency_overrides[get_qb_adapter] = override_qb
    monkeypatch.setattr(
        approval_routes,
        "open_qb_adapter",
        lambda _settings: fake_qb_context(fake),
    )
    monkeypatch.setattr(approval_routes, "get_settings", confirm_download_settings)
    monkeypatch.setattr(
        approval_routes,
        "evaluate_preflight",
        let_competing_request_commit,
    )

    async with phase3_api_client_factory() as client:
        replayed = await client.post(
            f"/api/candidates/{record.id}/confirm-download",
            json=request_body,
            headers=headers,
        )

    assert competing_response is not None
    assert competing_response.status_code == 201, competing_response.text
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["outcome"] == "EXECUTION_REPLAYED"
    assert (
        replayed.json()["execution"]["id"]
        == competing_response.json()["execution"]["id"]
    )
    async with session_factory() as session:
        executions = list((await session.scalars(select(DownloadExecution))).all())
        intents = list((await session.scalars(select(ExecutionIntent))).all())
    assert len(executions) == 1
    assert len(intents) == 1


@pytest.mark.asyncio
async def test_confirm_download_rejects_idempotency_key_binding_changes(
    session_factory: async_sessionmaker[AsyncSession],
    phase3_api_client_factory,
    monkeypatch,
) -> None:
    first = await seed_api_candidate(session_factory)
    second = await seed_api_candidate(session_factory, suffix="-second")
    fake = ApiFakeQb()

    async def override_qb() -> AsyncIterator[ReadOnlyDownloaderAdapter]:
        yield fake

    app.dependency_overrides[get_qb_adapter] = override_qb
    monkeypatch.setattr(
        approval_routes,
        "open_qb_adapter",
        lambda _settings: fake_qb_context(fake),
    )
    monkeypatch.setattr(approval_routes, "get_settings", confirm_download_settings)
    headers = {"Idempotency-Key": "confirm-download-api-key-0003"}
    request_body = {
        "acknowledges_hnr": False,
        "acknowledges_seeding": True,
        "acknowledges_plan_only": True,
        "launch_mode": "ADD_PAUSED",
    }

    async with phase3_api_client_factory() as client:
        created = await client.post(
            f"/api/candidates/{first.id}/confirm-download",
            json=request_body,
            headers=headers,
        )

        async def replay_gate_must_not_run(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("idempotency binding checks must run before stage gates")

        def replay_settings_must_not_load() -> Settings:
            raise AssertionError("idempotency binding checks must run before settings gates")

        def replay_qb_must_not_open(_settings: Settings) -> object:
            raise AssertionError("idempotency binding checks must not open qBittorrent")

        monkeypatch.setattr(
            approval_routes,
            "require_stage_not_disabled",
            replay_gate_must_not_run,
        )
        monkeypatch.setattr(approval_routes, "get_settings", replay_settings_must_not_load)
        monkeypatch.setattr(approval_routes, "open_qb_adapter", replay_qb_must_not_open)
        changed_candidate = await client.post(
            f"/api/candidates/{second.id}/confirm-download",
            json=request_body,
            headers=headers,
        )
        changed_mode = await client.post(
            f"/api/candidates/{first.id}/confirm-download",
            json={**request_body, "launch_mode": "START_IMMEDIATELY"},
            headers=headers,
        )
        changed_ttl = await client.post(
            f"/api/candidates/{first.id}/confirm-download",
            json={**request_body, "expires_in_minutes": 30},
            headers=headers,
        )
        missing_acknowledgement = await client.post(
            f"/api/candidates/{first.id}/confirm-download",
            json={**request_body, "acknowledges_seeding": False},
            headers=headers,
        )

    assert created.status_code == 201, created.text
    assert changed_candidate.status_code == 409
    assert changed_candidate.json()["error_code"] == "IDEMPOTENCY_KEY_REUSED"
    assert changed_mode.status_code == 409
    assert changed_mode.json()["error_code"] == "IDEMPOTENCY_KEY_REUSED"
    assert changed_ttl.status_code == 409
    assert changed_ttl.json()["error_code"] == "IDEMPOTENCY_KEY_REUSED"
    assert missing_acknowledgement.status_code == 422
    assert (
        missing_acknowledgement.json()["error_code"]
        == "APPROVAL_ACKNOWLEDGEMENTS_REQUIRED"
    )


@pytest.mark.asyncio
async def test_confirm_download_new_request_still_requires_stage_gate(
    session_factory: async_sessionmaker[AsyncSession],
    phase3_api_client_factory,
    monkeypatch,
) -> None:
    record = await seed_api_candidate(session_factory)

    async def disabled_stage(*_args: object, **_kwargs: object) -> None:
        raise AppError(
            "AUTOMATION_STAGE_DISABLED",
            "stage disabled",
            status_code=409,
        )

    def qb_must_not_open(_settings: Settings) -> object:
        raise AssertionError("qBittorrent must not open when a stage is disabled")

    monkeypatch.setattr(approval_routes, "require_stage_not_disabled", disabled_stage)
    monkeypatch.setattr(approval_routes, "open_qb_adapter", qb_must_not_open)

    async with phase3_api_client_factory() as client:
        response = await client.post(
            f"/api/candidates/{record.id}/confirm-download",
            json={
                "acknowledges_seeding": True,
                "acknowledges_plan_only": True,
            },
            headers={"Idempotency-Key": "confirm-download-new-stage-gate"},
        )

    assert response.status_code == 409
    assert response.json()["error_code"] == "AUTOMATION_STAGE_DISABLED"


@pytest.mark.asyncio
async def test_confirm_download_new_request_still_requires_control_plane_gate(
    session_factory: async_sessionmaker[AsyncSession],
    phase3_api_client_factory,
    monkeypatch,
) -> None:
    record = await seed_api_candidate(session_factory)

    def qb_must_not_open(_settings: Settings) -> object:
        raise AssertionError("qBittorrent must not open when the control plane is disabled")

    monkeypatch.setattr(approval_routes, "get_settings", api_settings)
    monkeypatch.setattr(approval_routes, "open_qb_adapter", qb_must_not_open)

    async with phase3_api_client_factory() as client:
        response = await client.post(
            f"/api/candidates/{record.id}/confirm-download",
            json={
                "acknowledges_seeding": True,
                "acknowledges_plan_only": True,
            },
            headers={"Idempotency-Key": "confirm-download-new-control-gate"},
        )

    assert response.status_code == 409
    assert response.json()["error_code"] == "DOWNLOAD_EXECUTION_CONTROL_PLANE_DISABLED"


@pytest.mark.asyncio
async def test_qb_endpoints_are_disabled_by_default_and_no_write_routes_exist(
    phase3_api_client_factory,
) -> None:
    async with phase3_api_client_factory() as client:
        status = await client.get("/api/downloaders/qbittorrent/status")
        torrents = await client.get("/api/downloaders/qbittorrent/torrents")
        openapi = (await client.get("/api/openapi.json")).json()
    assert status.status_code == 409
    assert status.json()["error_code"] == "QB_READ_ONLY_DISABLED"
    assert torrents.status_code == 409
    qb_paths = {
        path: sorted(methods)
        for path, methods in openapi["paths"].items()
        if path.startswith("/api/downloaders/qbittorrent")
    }
    assert qb_paths == {
        "/api/downloaders/qbittorrent/status": ["get"],
        "/api/downloaders/qbittorrent/torrents": ["get"],
    }


@pytest.mark.asyncio
async def test_approval_api_generates_plan_without_external_download(
    session_factory: async_sessionmaker[AsyncSession],
    phase3_api_client_factory,
    monkeypatch,
) -> None:
    record = await seed_api_candidate(session_factory)
    fake = ApiFakeQb(category_exists=False)

    async def override_qb() -> AsyncIterator[ReadOnlyDownloaderAdapter]:
        yield fake

    app.dependency_overrides[get_qb_adapter] = override_qb
    monkeypatch.setattr(approval_routes, "get_settings", api_settings)

    async with phase3_api_client_factory() as client:
        created = await client.post(
            f"/api/candidates/{record.id}/approval-requests",
            json={"operator": "api-reviewer", "expires_in_minutes": 60},
        )
        assert created.status_code == 201
        approval_id = created.json()["id"]
        duplicate = await client.post(
            f"/api/candidates/{record.id}/approval-requests",
            json={"operator": "other-reviewer"},
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error_code"] == "APPROVAL_REQUEST_DUPLICATE"

        approved = await client.post(
            f"/api/approval-requests/{approval_id}/approve",
            json={
                "operator": "api-reviewer",
                "acknowledges_hnr": True,
                "acknowledges_seeding": True,
                "acknowledges_plan_only": True,
            },
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "APPROVED"
        assert approved.json()["preflight_result"]["overall_status"] == "WARNING"
        category_check = next(
            check
            for check in approved.json()["preflight_result"]["checks"]
            if check["code"] == "TARGET_CATEGORY"
        )
        assert category_check["status"] == "WARNING"
        assert category_check["details"] == {
            "category": "movies",
            "will_create_on_submit": True,
        }
        plan = await client.get(f"/api/approval-requests/{approval_id}/download-plan")
        assert plan.status_code == 200

    serialized = plan.text.casefold()
    assert "movies-root" in serialized
    for forbidden in (
        "download_url",
        "announce",
        "passkey",
        "cookie",
        "bearer",
        "password",
        "pid",
    ):
        assert forbidden not in serialized
    assert fake.calls == [
        "authenticate",
        "get_version",
        "get_web_api_version",
        "list_torrents",
        "list_torrents",
        "list_torrents",
        "get_categories",
    ]


@pytest.mark.asyncio
async def test_approve_runs_preflight_and_returns_block_reasons_without_plan(
    session_factory: async_sessionmaker[AsyncSession],
    phase3_api_client_factory,
    monkeypatch,
) -> None:
    record = await seed_api_candidate(session_factory)
    fake = ApiFakeQb(duplicate_info_hash=True)

    async def override_qb() -> AsyncIterator[ReadOnlyDownloaderAdapter]:
        yield fake

    app.dependency_overrides[get_qb_adapter] = override_qb
    monkeypatch.setattr(approval_routes, "get_settings", api_settings)

    async with phase3_api_client_factory() as client:
        created = await client.post(
            f"/api/candidates/{record.id}/approval-requests",
            json={"expires_in_minutes": 60},
        )
        approval_id = created.json()["id"]
        blocked = await client.post(
            f"/api/approval-requests/{approval_id}/approve",
            json={
                "acknowledges_hnr": True,
                "acknowledges_seeding": True,
                "acknowledges_plan_only": True,
            },
        )
        plan = await client.get(f"/api/approval-requests/{approval_id}/download-plan")

    assert blocked.status_code == 200
    body = blocked.json()
    assert body["status"] == "PENDING"
    assert body["preflight_result"]["overall_status"] == "BLOCKED"
    duplicate_check = next(
        check
        for check in body["preflight_result"]["checks"]
        if check["code"] == "DUPLICATE_INFO_HASH"
    )
    assert duplicate_check["status"] == "BLOCKED"
    assert duplicate_check["message"]
    assert plan.status_code == 404

    async with session_factory() as session:
        assert await session.scalar(select(DownloadPlan)) is None
        events = list(
            (
                await session.scalars(
                    select(ApprovalEvent).where(
                        ApprovalEvent.approval_request_id == approval_id
                    )
                )
            ).all()
        )
    assert [event.event_type for event in events] == ["REQUESTED", "PREFLIGHT_COMPLETED"]


@pytest.mark.asyncio
async def test_approve_rejects_missing_acknowledgements_before_qb_read(
    session_factory: async_sessionmaker[AsyncSession],
    phase3_api_client_factory,
    monkeypatch,
) -> None:
    record = await seed_api_candidate(session_factory)
    fake = ApiFakeQb()

    async def override_qb() -> AsyncIterator[ReadOnlyDownloaderAdapter]:
        yield fake

    app.dependency_overrides[get_qb_adapter] = override_qb
    monkeypatch.setattr(approval_routes, "get_settings", api_settings)

    async with phase3_api_client_factory() as client:
        created = await client.post(
            f"/api/candidates/{record.id}/approval-requests",
            json={"expires_in_minutes": 60},
        )
        rejected = await client.post(
            f"/api/approval-requests/{created.json()['id']}/approve",
            json={
                "acknowledges_hnr": False,
                "acknowledges_seeding": False,
                "acknowledges_plan_only": True,
            },
        )

    assert rejected.status_code == 422
    assert rejected.json()["error_code"] == "APPROVAL_ACKNOWLEDGEMENTS_REQUIRED"
    assert fake.calls == []


@pytest.mark.asyncio
async def test_approve_rechecks_disabled_policy_after_external_preflight(
    session_factory: async_sessionmaker[AsyncSession],
    phase3_api_client_factory,
    monkeypatch,
) -> None:
    record = await seed_api_candidate(session_factory)
    fake = ApiFakeQb()

    async def override_qb() -> AsyncIterator[ReadOnlyDownloaderAdapter]:
        yield fake

    real_evaluate_preflight = approval_routes.evaluate_preflight

    async def disable_approval_during_preflight(*args, **kwargs):
        result = await real_evaluate_preflight(*args, **kwargs)
        async with session_factory() as session:
            _, current = await get_current_policy(session, for_update=True)
            await publish_policy_revision(
                session,
                AutomationPolicyRevisionCreateRequest(
                    base_revision_no=current.revision_no,
                    identity_mode=AutomationMode.MANUAL,
                    torrent_selection_mode=AutomationMode.MANUAL,
                    approval_mode=AutomationMode.DISABLED,
                    execution_mode=AutomationMode.MANUAL,
                ),
                actor="concurrent-admin",
            )
            await session.commit()
        return result

    app.dependency_overrides[get_qb_adapter] = override_qb
    monkeypatch.setattr(approval_routes, "get_settings", api_settings)
    monkeypatch.setattr(
        approval_routes,
        "evaluate_preflight",
        disable_approval_during_preflight,
    )

    async with phase3_api_client_factory() as client:
        created = await client.post(
            f"/api/candidates/{record.id}/approval-requests",
            json={"expires_in_minutes": 60},
        )
        approval_id = created.json()["id"]
        rejected = await client.post(
            f"/api/approval-requests/{approval_id}/approve",
            json={
                "acknowledges_hnr": True,
                "acknowledges_seeding": True,
                "acknowledges_plan_only": True,
            },
        )

    assert rejected.status_code == 409
    assert rejected.json()["error_code"] == "AUTOMATION_STAGE_DISABLED"
    assert rejected.json()["details"]["stage"] == "APPROVAL"
    assert fake.calls

    async with session_factory() as session:
        approval = await session.get(ApprovalRequest, approval_id)
        assert approval is not None
        assert approval.status == ApprovalStatus.PENDING
        assert approval.preflight_result is None
        assert await session.scalar(select(DownloadPlan)) is None
        events = list(
            (
                await session.scalars(
                    select(ApprovalEvent).where(
                        ApprovalEvent.approval_request_id == approval_id
                    )
                )
            ).all()
        )
    assert [event.event_type for event in events] == ["REQUESTED"]


@pytest.mark.asyncio
async def test_approve_discards_preflight_when_target_config_changes_during_read(
    session_factory: async_sessionmaker[AsyncSession],
    phase3_api_client_factory,
    monkeypatch,
) -> None:
    record = await seed_api_candidate(session_factory)
    fake = ApiFakeQb()
    initial_settings = api_settings()
    changed_settings = initial_settings.model_copy(
        update={"qb_target_category": "changed-during-preflight"}
    )
    effective_settings = initial_settings

    async def override_qb() -> AsyncIterator[ReadOnlyDownloaderAdapter]:
        yield fake

    real_evaluate_preflight = approval_routes.evaluate_preflight

    async def change_target_during_preflight(*args, **kwargs):
        nonlocal effective_settings
        result = await real_evaluate_preflight(*args, **kwargs)
        effective_settings = changed_settings
        return result

    app.dependency_overrides[get_qb_adapter] = override_qb
    monkeypatch.setattr(approval_routes, "get_settings", lambda: effective_settings)
    monkeypatch.setattr(
        approval_routes,
        "evaluate_preflight",
        change_target_during_preflight,
    )

    async with phase3_api_client_factory() as client:
        created = await client.post(
            f"/api/candidates/{record.id}/approval-requests",
            json={"expires_in_minutes": 60},
        )
        approval_id = created.json()["id"]
        rejected = await client.post(
            f"/api/approval-requests/{approval_id}/approve",
            json={
                "acknowledges_hnr": True,
                "acknowledges_seeding": True,
                "acknowledges_plan_only": True,
            },
        )

        assert rejected.status_code == 409
        assert rejected.json()["error_code"] == "PREFLIGHT_CONFIG_CHANGED"

        # A failed phase-two check must release its transaction and leave the request
        # retryable against a fresh, internally consistent configuration snapshot.
        effective_settings = initial_settings
        monkeypatch.setattr(
            approval_routes,
            "evaluate_preflight",
            real_evaluate_preflight,
        )
        approved = await client.post(
            f"/api/approval-requests/{approval_id}/approve",
            json={
                "acknowledges_hnr": True,
                "acknowledges_seeding": True,
                "acknowledges_plan_only": True,
            },
        )

    assert approved.status_code == 200
    assert approved.json()["status"] == "APPROVED"

    async with session_factory() as session:
        approval = await session.get(ApprovalRequest, approval_id)
        assert approval is not None
        assert approval.status == ApprovalStatus.APPROVED
        plan = await session.scalar(
            select(DownloadPlan).where(DownloadPlan.approval_id == approval_id)
        )
        assert plan is not None
        assert plan.category == initial_settings.qb_target_category
        events = list(
            (
                await session.scalars(
                    select(ApprovalEvent).where(
                        ApprovalEvent.approval_request_id == approval_id
                    )
                )
            ).all()
        )
    assert [event.event_type for event in events] == [
        "REQUESTED",
        "PREFLIGHT_COMPLETED",
        "APPROVED",
        "DOWNLOAD_PLAN_CREATED",
    ]


@pytest.mark.asyncio
async def test_approval_expiring_during_combined_preflight_is_audited_and_not_approved(
    session_factory: async_sessionmaker[AsyncSession],
    phase3_api_client_factory,
    monkeypatch,
) -> None:
    record = await seed_api_candidate(session_factory)
    expired_during_read = False

    class ExpiringFakeQb(ApiFakeQb):
        async def authenticate(self) -> None:
            nonlocal expired_during_read
            await super().authenticate()
            expired_during_read = True

    fake = ExpiringFakeQb()

    async def override_qb() -> AsyncIterator[ReadOnlyDownloaderAdapter]:
        yield fake

    app.dependency_overrides[get_qb_adapter] = override_qb
    monkeypatch.setattr(approval_routes, "get_settings", api_settings)

    async with phase3_api_client_factory() as client:
        created = await client.post(
            f"/api/candidates/{record.id}/approval-requests",
            json={"expires_in_minutes": 60},
        )
        approval_id = created.json()["id"]
        expires_at = datetime.fromisoformat(created.json()["expires_at"])
        monkeypatch.setattr(
            approval_services,
            "utc_now",
            lambda: expires_at + timedelta(seconds=1)
            if expired_during_read
            else expires_at - timedelta(seconds=1),
        )
        expired = await client.post(
            f"/api/approval-requests/{approval_id}/approve",
            json={
                "acknowledges_hnr": True,
                "acknowledges_seeding": True,
                "acknowledges_plan_only": True,
            },
        )

    assert expired.status_code == 409
    assert expired.json()["error_code"] == "APPROVAL_EXPIRED"
    async with session_factory() as session:
        stored = await session.get(ApprovalRequest, approval_id)
        assert stored is not None
        assert stored.status == ApprovalStatus.EXPIRED
        assert stored.preflight_result is None
        assert await session.scalar(select(DownloadPlan)) is None
        events = list(
            (
                await session.scalars(
                    select(ApprovalEvent).where(
                        ApprovalEvent.approval_request_id == approval_id
                    )
                )
            ).all()
        )
    assert [event.event_type for event in events] == ["REQUESTED", "EXPIRED"]


@pytest.mark.asyncio
async def test_expired_approval_error_persists_status_and_audit_event(
    session_factory: async_sessionmaker[AsyncSession],
    phase3_api_client_factory,
    monkeypatch,
) -> None:
    record = await seed_api_candidate(session_factory)
    monkeypatch.setattr(approval_routes, "get_settings", api_settings)
    fake = ApiFakeQb()

    async def override_qb() -> AsyncIterator[ReadOnlyDownloaderAdapter]:
        yield fake

    app.dependency_overrides[get_qb_adapter] = override_qb

    async with phase3_api_client_factory() as client:
        created = await client.post(
            f"/api/candidates/{record.id}/approval-requests",
            json={"operator": "expiry-reviewer", "expires_in_minutes": 60},
        )
        assert created.status_code == 201
        approval_id = created.json()["id"]

        expires_at = datetime.fromisoformat(created.json()["expires_at"])
        monkeypatch.setattr(
            approval_services,
            "utc_now",
            lambda: expires_at + timedelta(seconds=1),
        )

        expired = await client.post(
            f"/api/approval-requests/{approval_id}/approve",
            json={
                "operator": "expiry-reviewer",
                "acknowledges_hnr": True,
                "acknowledges_seeding": True,
                "acknowledges_plan_only": True,
            },
        )
        repeated = await client.post(
            f"/api/approval-requests/{approval_id}/preflight",
            json={"operator": "expiry-reviewer"},
        )

    assert expired.status_code == 409
    assert expired.json()["error_code"] == "APPROVAL_EXPIRED"
    assert repeated.status_code == 409
    assert repeated.json()["error_code"] == "APPROVAL_EXPIRED"
    assert fake.calls == []
    async with session_factory() as session:
        stored = await session.get(ApprovalRequest, approval_id)
        assert stored is not None
        assert stored.status == ApprovalStatus.EXPIRED
        events = list(
            (
                await session.scalars(
                    select(ApprovalEvent).where(
                        ApprovalEvent.approval_request_id == approval_id,
                        ApprovalEvent.event_type == "EXPIRED",
                    )
                )
            ).all()
        )
    assert len(events) == 1
    assert events[0].from_status == ApprovalStatus.PENDING.value
    assert events[0].to_status == ApprovalStatus.EXPIRED.value
    assert events[0].actor == "system"
