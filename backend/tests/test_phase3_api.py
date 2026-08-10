from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.api.routes.approvals as approval_routes
import app.services.approvals as approval_services
from app.adapters.base import ReadOnlyDownloaderAdapter
from app.api.dependencies import get_qb_adapter
from app.core.config import Settings
from app.db.session import get_session
from app.main import app
from app.models.entities import (
    ApprovalEvent,
    ApprovalRequest,
    MediaItem,
    TorrentCandidateRecord,
    TorrentSearchRun,
)
from app.models.enums import (
    ApprovalStatus,
    IdentityConfidence,
    MediaType,
    MetadataStatus,
    WorkflowStatus,
)
from app.schemas.adapters import AdapterManifest, TorrentCandidate
from app.schemas.qbittorrent import QbCategory, QbTorrent, QbTorrentFile

INFO_HASH = "1234567890abcdef1234567890abcdef12345678"


class ApiFakeQb(ReadOnlyDownloaderAdapter):
    def __init__(self) -> None:
        self.calls: list[str] = []

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
                hash="f" * 40,
                name="Existing Seeder",
                size=100,
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
        return {"movies": QbCategory(name="movies", savePath="/downloads/movies")}


@pytest.fixture
def phase3_api_client_factory(session_factory: async_sessionmaker[AsyncSession]):
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session

    def create() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )

    yield create
    app.dependency_overrides.clear()


async def seed_api_candidate(
    session_factory: async_sessionmaker[AsyncSession],
) -> TorrentCandidateRecord:
    now = datetime.now(UTC)
    media = MediaItem(
        source="nextfind",
        source_item_id="phase3-api-media",
        media_type=MediaType.MOVIE,
        tmdb_id=333,
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
        torrent_id="phase3-candidate",
        release_title="Phase 3 Movie 2026 1080p",
        details_ref="avistaz:details:phase3-safe",
        media_type=MediaType.MOVIE,
        tmdb_id=333,
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
        qb_target_category="movies",
        qb_target_save_path="/downloads/movies/incoming",
        qb_allowed_save_paths=("/downloads/movies",),
        qb_save_path_ref="movies-root",
        qb_plan_tags=("unin-plan",),
        max_candidate_size_bytes=10_000,
        approval_default_ttl_minutes=60,
    )


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
    fake = ApiFakeQb()

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

        preflight = await client.post(
            f"/api/approval-requests/{approval_id}/preflight",
            json={"operator": "api-reviewer"},
        )
        assert preflight.status_code == 200
        assert preflight.json()["preflight_result"]["overall_status"] == "PASS"

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
        "get_categories",
    ]


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
