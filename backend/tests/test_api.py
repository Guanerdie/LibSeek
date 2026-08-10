from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import (
    get_admin_principal,
    get_operator_principal,
    get_viewer_principal,
)
from app.core.auth import Principal
from app.db.session import get_session
from app.main import app
from app.models.entities import MediaItem, MetadataMatch, TorrentCandidateRecord, TorrentSearchRun
from app.models.enums import (
    AuthRole,
    IdentityConfidence,
    MediaType,
    MetadataStatus,
    WorkflowStatus,
)
from app.schemas.adapters import MetadataRecord, TorrentCandidate


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
        missing = await client.get("/api/media/not-found")
        assert missing.status_code == 404
        assert missing.json()["error_code"] == "MEDIA_NOT_FOUND"


@pytest.mark.asyncio
async def test_adapters_expose_no_secrets_and_phase_flags(api_client_factory) -> None:
    async with api_client_factory() as client:
        response = await client.get("/api/adapters")
        assert response.status_code == 200
        payload = response.json()
        avistaz = next(item for item in payload if item["id"] == "avistaz-mock")
        assert avistaz["enabled"] is False
        serialized = response.text.lower()
        for forbidden in ("password", "cookie", "authorization", "passkey"):
            assert forbidden not in serialized


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
        search = await client.post(f"/api/media/{item.id}/torrent-searches", json={})
        assert search.status_code == 409
        assert search.json()["error_code"] == "AVISTAZ_LIVE_DISABLED"


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
