from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import (
    get_admin_principal,
    get_operator_principal,
    get_viewer_principal,
)
from app.api.routes import health as health_routes
from app.api.routes import workflow as workflow_routes
from app.core.auth import Principal
from app.core.config import Settings
from app.db.session import get_session
from app.main import app
from app.models.entities import (
    AuditEvent,
    IdentityReview,
    Job,
    MediaItem,
    MetadataMatch,
    TorrentCandidateRecord,
    TorrentSearchRun,
    WorkerHeartbeat,
)
from app.models.enums import (
    AuthRole,
    IdentityConfidence,
    JobStatus,
    MediaType,
    MetadataStatus,
    WorkflowStatus,
)
from app.schemas.adapters import MetadataRecord, TorrentCandidate
from app.services.automation_policy import get_current_policy
from app.workers.identity import job_worker_id


@pytest.fixture
def api_client_factory(session_factory: async_sessionmaker[AsyncSession]):
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    principal = Principal(
        username="api-test-admin",
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


@pytest.mark.asyncio
async def test_health_system_and_validation_errors(api_client_factory) -> None:
    async with api_client_factory() as client:
        health = await client.get("/api/health")
        assert health.json() == {"status": "ok"}
        system = await client.get("/api/system/status")
        assert system.status_code == 200
        assert system.json()["postgres"]["healthy"] is True
        invalid = await client.get("/api/media?page_size=999")
        assert invalid.status_code == 422
        assert invalid.json()["error_code"] == "API_VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_system_status_reports_runtime_gates_without_exposing_secrets(
    api_client_factory,
    monkeypatch,
) -> None:
    settings = Settings(
        _env_file=None,
        nextfind_username="nextfind-user",
        nextfind_password="nextfind-secret-value",
        tmdb_access_token="tmdb-secret-value",
        avistaz_username="avistaz-user",
        avistaz_password="avistaz-secret-value",
        avistaz_pid="avistaz-pid-secret-value",
        qb_base_url="https://qb.internal.test",
        qb_username="qb-user",
        qb_password="qb-secret-value",
        qb_allowed_hosts=("qb.internal.test",),
        enable_download_execution_control_plane=True,
        enable_download_executor=True,
        enable_avistaz_torrent_fetch=False,
        enable_qb_write=True,
        enable_download_monitor=False,
        enable_automation_engine=True,
    )
    monkeypatch.setattr(health_routes, "get_settings", lambda: settings)

    async with api_client_factory() as client:
        response = await client.get("/api/system/status")

    assert response.status_code == 200
    payload = response.json()
    assert {
        key: payload[key]
        for key in (
            "download_control_plane_enabled",
            "download_executor_enabled",
            "avistaz_torrent_fetch_enabled",
            "qb_write_enabled",
            "download_monitor_enabled",
            "automation_engine_enabled",
        )
    } == {
        "download_control_plane_enabled": True,
        "download_executor_enabled": True,
        "avistaz_torrent_fetch_enabled": False,
        "qb_write_enabled": True,
        "download_monitor_enabled": False,
        "automation_engine_enabled": True,
    }
    for secret in (
        "nextfind-secret-value",
        "tmdb-secret-value",
        "avistaz-secret-value",
        "avistaz-pid-secret-value",
        "qb-secret-value",
    ):
        assert secret not in response.text


@pytest.mark.asyncio
async def test_system_status_ignores_specialized_worker_heartbeats(
    session_factory: async_sessionmaker[AsyncSession], api_client_factory
) -> None:
    now = datetime.now(UTC)
    job_worker = WorkerHeartbeat(
        worker_id=job_worker_id("api-status"),
        last_seen_at=now - timedelta(minutes=5),
    )
    async with session_factory() as session:
        session.add_all(
            [
                job_worker,
                WorkerHeartbeat(
                    worker_id="automation-preflight:fresh-specialized-worker",
                    last_seen_at=now,
                ),
                WorkerHeartbeat(
                    worker_id="download-executor:fresh-specialized-worker",
                    last_seen_at=now,
                ),
            ]
        )
        await session.commit()

    async with api_client_factory() as client:
        stale = await client.get("/api/system/status")
        assert stale.status_code == 200
        assert stale.json()["worker"]["healthy"] is False
        assert stale.json()["worker"]["message"] == "Worker 心跳已过期"

    async with session_factory() as session:
        refreshed = await session.get(WorkerHeartbeat, job_worker.worker_id)
        assert refreshed is not None
        refreshed.last_seen_at = datetime.now(UTC)
        await session.commit()

    async with api_client_factory() as client:
        healthy = await client.get("/api/system/status")
        assert healthy.status_code == 200
        assert healthy.json()["worker"]["healthy"] is True
        assert healthy.json()["worker"]["message"] == "Worker 运行正常"


@pytest.mark.asyncio
async def test_media_pagination_and_not_found(
    session_factory: async_sessionmaker[AsyncSession], api_client_factory
) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add(
            MediaItem(
                source="nextfind",
                source_item_id="nextfind:1",
                media_type=MediaType.MOVIE,
                tmdb_id=1,
                title="Example",
                year=2026,
                country_codes=["JP", "US"],
                identity_confidence=IdentityConfidence.HIGH,
                metadata_status=MetadataStatus.RESOLVED,
                discovered_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    async with api_client_factory() as client:
        response = await client.get("/api/media?page=1&page_size=20&media_type=movie")
        assert response.status_code == 200
        assert response.json()["total"] == 1
        assert response.json()["items"][0]["title"] == "Example"
        assert response.json()["items"][0]["country_codes"] == ["JP", "US"]
        missing = await client.get("/api/media/not-found")
        assert missing.status_code == 404
        assert missing.json()["error_code"] == "MEDIA_NOT_FOUND"


@pytest.mark.asyncio
async def test_media_region_filter_uses_nextfind_country_groups_before_pagination(
    session_factory: async_sessionmaker[AsyncSession], api_client_factory
) -> None:
    now = datetime.now(UTC)
    fixtures = (
        ("japan-western", MediaType.TV, ["JP", "US"]),
        ("mainland", MediaType.MOVIE, ["CN"]),
        ("hong-kong-taiwan", MediaType.TV, ["TW"]),
        ("korea", MediaType.TV, ["KR"]),
        ("asia-pacific", MediaType.TV, ["PH"]),
        ("other", MediaType.TV, ["NZ"]),
        ("unknown", MediaType.TV, None),
    )
    async with session_factory() as session:
        session.add_all(
            MediaItem(
                source="nextfind",
                source_item_id=f"nextfind:{source_id}",
                media_type=media_type,
                tmdb_id=index + 100,
                title=source_id,
                year=2026,
                country_codes=country_codes,
                identity_confidence=IdentityConfidence.HIGH,
                metadata_status=MetadataStatus.RESOLVED,
                discovered_at=now,
                updated_at=now + timedelta(seconds=index),
            )
            for index, (source_id, media_type, country_codes) in enumerate(fixtures)
        )
        await session.commit()

    async with api_client_factory() as client:
        japan = await client.get("/api/media?region=japan&page=1&page_size=1")
        western = await client.get("/api/media?region=western")
        mainland_movies = await client.get(
            "/api/media?region=mainland&media_type=movie&query=main"
        )
        hong_kong_taiwan = await client.get(
            "/api/media?region=hong-kong-taiwan"
        )
        korea = await client.get("/api/media?region=korea")
        asia_pacific = await client.get("/api/media?region=asia-pacific")
        invalid = await client.get("/api/media?region=antarctica")

    assert japan.status_code == 200
    assert japan.json()["total"] == 1
    assert [item["title"] for item in japan.json()["items"]] == ["japan-western"]
    assert western.status_code == 200
    assert [item["title"] for item in western.json()["items"]] == ["japan-western"]
    assert mainland_movies.status_code == 200
    assert [item["title"] for item in mainland_movies.json()["items"]] == ["mainland"]
    assert [item["title"] for item in hong_kong_taiwan.json()["items"]] == [
        "hong-kong-taiwan"
    ]
    assert [item["title"] for item in korea.json()["items"]] == ["korea"]
    assert [item["title"] for item in asia_pacific.json()["items"]] == [
        "asia-pacific"
    ]
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_adapters_expose_no_secrets_and_phase_flags(api_client_factory) -> None:
    async with api_client_factory() as client:
        response = await client.get("/api/adapters")
        assert response.status_code == 200
        payload = response.json()
        adapter_ids = {item["id"] for item in payload}
        assert {"nextfind", "tmdb", "avistaz", "qbittorrent-read-only"} <= adapter_ids
        assert "tmdb-mock" not in adapter_ids
        assert "avistaz-mock" not in adapter_ids
        serialized = response.text.lower()
        for forbidden in ("password", "cookie", "authorization", "passkey"):
            assert forbidden not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_id", ("tmdb-mock", "avistaz-mock"))
async def test_mock_adapters_are_not_publicly_addressable(
    api_client_factory,
    adapter_id: str,
) -> None:
    async with api_client_factory() as client:
        response = await client.get(f"/api/adapters/{adapter_id}/capabilities")

    assert response.status_code == 404
    assert response.json()["error_code"] == "ADAPTER_NOT_FOUND"


@pytest.mark.asyncio
async def test_discovery_requires_runtime_configuration(api_client_factory) -> None:
    async with api_client_factory() as client:
        response = await client.post("/api/discovery-runs")
        assert response.status_code == 409
        assert response.json()["error_code"] == "NEXTFIND_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_live_read_only_endpoints_are_disabled_by_default(
    session_factory: async_sessionmaker[AsyncSession], api_client_factory
) -> None:
    now = datetime.now(UTC)
    item = MediaItem(
        source="nextfind",
        source_item_id="nextfind:phase2-disabled",
        media_type=MediaType.MOVIE,
        tmdb_id=10,
        title="Disabled",
        identity_confidence=IdentityConfidence.HIGH,
        metadata_status=MetadataStatus.UNRESOLVED,
        discovered_at=now,
        updated_at=now,
    )
    async with session_factory() as session:
        session.add(item)
        await session.commit()
    async with api_client_factory() as client:
        resolve = await client.post(f"/api/media/{item.id}/resolve")
        assert resolve.status_code == 409
        assert resolve.json()["error_code"] == "TMDB_LIVE_DISABLED"
        search = await client.post(
            f"/api/media/{item.id}/torrent-searches",
            json={"site_id": "avistaz"},
        )
        assert search.status_code == 409
        assert search.json()["error_code"] == "AVISTAZ_LIVE_DISABLED"


@pytest.mark.asyncio
async def test_metadata_resolution_job_status_is_bound_and_sanitized(
    session_factory: async_sessionmaker[AsyncSession], api_client_factory
) -> None:
    now = datetime.now(UTC)
    item = MediaItem(
        source="nextfind",
        source_item_id="nextfind:resolution-job-status",
        media_type=MediaType.MOVIE,
        title="Resolution Job Status",
        identity_confidence=IdentityConfidence.NEEDS_CONFIRMATION,
        metadata_status=MetadataStatus.UNRESOLVED,
        workflow_status=WorkflowStatus.METADATA_PENDING,
        discovered_at=now,
        updated_at=now,
    )
    async with session_factory() as session:
        session.add(item)
        await session.flush()
        job = Job(
            job_type=f"RESOLVE_METADATA:{item.id}",
            status=JobStatus.RETRY_WAIT,
            payload={
                "media_id": item.id,
                "read_only": True,
                "access_token": "payload-secret-must-not-leak",
            },
            attempts=2,
            max_attempts=7,
            locked_by="internal-worker-name",
            lease_token="internal-lease-token",
            error_code="TMDB_TEMPORARILY_UNAVAILABLE",
            error_message="TMDB 暂时不可用",
        )
        session.add(job)
        await session.commit()

    async with api_client_factory() as client:
        response = await client.get(f"/api/media/{item.id}/resolve-jobs/{job.id}")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert set(response.json()) == {
        "media_id",
        "job_id",
        "status",
        "error_code",
        "error_message",
        "created_at",
        "updated_at",
    }
    payload = response.json()
    assert payload["media_id"] == item.id
    assert payload["job_id"] == job.id
    assert payload["status"] == "RETRY_WAIT"
    assert payload["error_code"] == "TMDB_TEMPORARILY_UNAVAILABLE"
    assert payload["error_message"] == "TMDB 暂时不可用"
    assert isinstance(payload["created_at"], str) and payload["created_at"]
    assert isinstance(payload["updated_at"], str) and payload["updated_at"]
    serialized = response.text
    for forbidden in (
        "payload-secret-must-not-leak",
        "internal-worker-name",
        "internal-lease-token",
        '"payload"',
        '"attempts"',
        '"max_attempts"',
        '"locked_by"',
        '"lease_token"',
    ):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_metadata_resolution_job_status_hides_missing_and_other_job_types(
    session_factory: async_sessionmaker[AsyncSession], api_client_factory
) -> None:
    now = datetime.now(UTC)
    item = MediaItem(
        source="nextfind",
        source_item_id="nextfind:resolution-job-target",
        media_type=MediaType.MOVIE,
        title="Resolution Job Target",
        identity_confidence=IdentityConfidence.NEEDS_CONFIRMATION,
        metadata_status=MetadataStatus.UNRESOLVED,
        discovered_at=now,
        updated_at=now,
    )
    other = MediaItem(
        source="nextfind",
        source_item_id="nextfind:resolution-job-other",
        media_type=MediaType.MOVIE,
        title="Resolution Job Other",
        identity_confidence=IdentityConfidence.NEEDS_CONFIRMATION,
        metadata_status=MetadataStatus.UNRESOLVED,
        discovered_at=now,
        updated_at=now,
    )
    async with session_factory() as session:
        session.add_all([item, other])
        await session.flush()
        other_job = Job(
            job_type=f"RESOLVE_METADATA:{other.id}",
            status=JobStatus.SUCCEEDED,
            payload={"media_id": other.id, "private_marker": "other-job-secret"},
        )
        session.add(other_job)
        await session.commit()

    async with api_client_factory() as client:
        missing_media = await client.get(
            f"/api/media/missing-media/resolve-jobs/{other_job.id}"
        )
        missing_job = await client.get(
            f"/api/media/{item.id}/resolve-jobs/missing-job"
        )
        other_job_response = await client.get(
            f"/api/media/{item.id}/resolve-jobs/{other_job.id}"
        )

    assert missing_media.status_code == 404
    assert missing_media.json()["error_code"] == "MEDIA_NOT_FOUND"
    for response in (missing_job, other_job_response):
        assert response.status_code == 404
        assert response.json()["error_code"] == "METADATA_RESOLUTION_JOB_NOT_FOUND"
        assert "other-job-secret" not in response.text
        assert "RESOLVE_METADATA" not in response.text


@pytest.mark.asyncio
async def test_metadata_resolution_job_status_rejects_payload_binding_mismatch(
    session_factory: async_sessionmaker[AsyncSession], api_client_factory
) -> None:
    now = datetime.now(UTC)
    item = MediaItem(
        source="nextfind",
        source_item_id="nextfind:resolution-binding-target",
        media_type=MediaType.MOVIE,
        title="Resolution Binding Target",
        identity_confidence=IdentityConfidence.NEEDS_CONFIRMATION,
        metadata_status=MetadataStatus.UNRESOLVED,
        discovered_at=now,
        updated_at=now,
    )
    async with session_factory() as session:
        session.add(item)
        await session.flush()
        job = Job(
            job_type=f"RESOLVE_METADATA:{item.id}",
            status=JobStatus.FAILED,
            payload={"media_id": "different-media", "private_marker": "binding-secret"},
            error_code="SHOULD_NOT_BE_RETURNED",
            error_message="should not be returned",
        )
        session.add(job)
        await session.commit()

    async with api_client_factory() as client:
        response = await client.get(f"/api/media/{item.id}/resolve-jobs/{job.id}")

    assert response.status_code == 409
    assert response.json()["error_code"] == "METADATA_RESOLUTION_JOB_BINDING_INVALID"
    assert "binding-secret" not in response.text
    assert "SHOULD_NOT_BE_RETURNED" not in response.text
    assert "should not be returned" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence_kind", ("confirmed_review", "run_only"))
async def test_resolve_api_rejects_durable_identity_evidence_without_writes(
    session_factory: async_sessionmaker[AsyncSession],
    api_client_factory,
    monkeypatch: pytest.MonkeyPatch,
    evidence_kind: str,
) -> None:
    settings = Settings(
        _env_file=None,
        enable_tmdb_live=True,
        tmdb_access_token="test-tmdb-token",
    )
    monkeypatch.setattr(workflow_routes, "get_settings", lambda: settings)
    now = datetime.now(UTC)
    item = MediaItem(
        source="nextfind",
        source_item_id=f"nextfind:resolve-evidence-{evidence_kind}",
        media_type=MediaType.MOVIE,
        tmdb_id=501,
        title="Evidence Movie",
        identity_confidence=IdentityConfidence.HIGH,
        metadata_status=MetadataStatus.RESOLVED,
        workflow_status=WorkflowStatus.IDENTITY_REVIEW,
        discovered_at=now,
        updated_at=now,
    )
    candidate = MetadataRecord(
        tmdb_id=501,
        media_type=MediaType.MOVIE,
        title="Evidence Movie",
        english_title="Evidence Movie",
        confidence=1,
    )
    async with session_factory() as session:
        await get_current_policy(session)
        session.add(item)
        await session.flush()
        if evidence_kind == "confirmed_review":
            match = MetadataMatch(
                media_id=item.id,
                tmdb_id=501,
                rank=1,
                score=1,
                match_reasons=["TMDB_ID_EXACT"],
                conflicts=[],
                candidate_snapshot=candidate.model_dump(mode="json"),
            )
            session.add(match)
            await session.flush()
            session.add(
                IdentityReview(
                    media_id=item.id,
                    metadata_match_id=match.id,
                    status="CONFIRMED",
                    confirmed_by="api-test-admin",
                    candidate_snapshot=candidate.model_dump(mode="json"),
                )
            )
        else:
            session.add(
                TorrentSearchRun(
                    media_id=item.id,
                    site_id="avistaz",
                    status=WorkflowStatus.TORRENT_REVIEW,
                    sanitized_request={},
                )
            )
        await session.commit()
        jobs_before = await session.scalar(select(func.count()).select_from(Job))
        audits_before = await session.scalar(select(func.count()).select_from(AuditEvent))

    async with api_client_factory() as client:
        response = await client.post(f"/api/media/{item.id}/resolve")

    assert response.status_code == 409
    assert response.json()["error_code"] == "IDENTITY_ALREADY_CONFIRMED"
    assert "test-tmdb-token" not in response.text
    async with session_factory() as session:
        refreshed = await session.get(MediaItem, item.id)
        assert refreshed is not None
        assert refreshed.workflow_status == WorkflowStatus.IDENTITY_REVIEW
        assert refreshed.metadata_status == MetadataStatus.RESOLVED
        assert await session.scalar(select(func.count()).select_from(Job)) == jobs_before
        assert await session.scalar(select(func.count()).select_from(AuditEvent)) == audits_before


@pytest.mark.asyncio
async def test_identity_confirmation_and_candidate_api_are_read_only_and_sanitized(
    session_factory: async_sessionmaker[AsyncSession], api_client_factory
) -> None:
    now = datetime.now(UTC)
    item = MediaItem(
        source="nextfind",
        source_item_id="nextfind:phase2-api",
        media_type=MediaType.MOVIE,
        tmdb_id=None,
        title="API Movie",
        identity_confidence=IdentityConfidence.NEEDS_CONFIRMATION,
        metadata_status=MetadataStatus.NEEDS_CONFIRMATION,
        discovered_at=now,
        updated_at=now,
    )
    metadata = MetadataRecord(
        tmdb_id=55,
        imdb_id="tt0055",
        media_type=MediaType.MOVIE,
        title="API 电影",
        chinese_title="API 电影",
        english_title="API Movie",
        original_title="API Original",
        year=2026,
        confidence=1,
    )
    async with session_factory() as session:
        session.add(item)
        await session.flush()
        match = MetadataMatch(
            media_id=item.id,
            tmdb_id=55,
            rank=1,
            score=0.95,
            match_reasons=["TITLE_EXACT"],
            conflicts=[],
            candidate_snapshot=metadata.model_dump(mode="json"),
        )
        session.add(match)
        await session.commit()
    async with api_client_factory() as client:
        matches = await client.get(f"/api/media/{item.id}/metadata-candidates")
        assert matches.status_code == 200
        assert matches.json()[0]["candidate"]["tmdb_id"] == 55
        confirmation = await client.post(
            f"/api/media/{item.id}/identity-confirmations",
            json={"metadata_match_id": match.id, "operator": "api-operator"},
        )
        assert confirmation.status_code == 201
        duplicate = await client.post(
            f"/api/media/{item.id}/identity-confirmations",
            json={"metadata_match_id": match.id, "operator": "api-operator"},
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error_code"] == "IDENTITY_CONFIRMATION_DUPLICATE"

    candidate = TorrentCandidate(
        site_id="avistaz",
        torrent_id="api-candidate",
        release_title="API Movie 2026 1080p",
        details_ref="avistaz:details:api-safe",
        media_type=MediaType.MOVIE,
        tmdb_id=55,
        year=2026,
        resolution="1080p",
        seeders=3,
        match_score=0.9,
        match_reasons=["TMDB_ID_EXACT"],
        warnings=[],
    )
    async with session_factory() as session:
        run = TorrentSearchRun(
            media_id=item.id,
            site_id="avistaz",
            status=WorkflowStatus.TORRENT_REVIEW,
            candidate_count=1,
            strategy_log=[{"strategy": "TMDB_ID", "candidate_count": 1}],
            sanitized_request={},
        )
        session.add(run)
        await session.flush()
        session.add(
            TorrentCandidateRecord(
                search_run_id=run.id,
                site_id="avistaz",
                torrent_id="api-candidate",
                candidate_snapshot=candidate.model_dump(mode="json"),
                match_score=0.9,
                match_reasons=["TMDB_ID_EXACT"],
                warnings=[],
            )
        )
        await session.commit()
    async with api_client_factory() as client:
        response = await client.get(f"/api/torrent-searches/{run.id}/candidates")
        assert response.status_code == 200
        serialized = response.text.casefold()
        assert "api movie" in serialized
        assert "https://" not in serialized
        assert "announce" not in serialized
        assert "passkey" not in serialized
