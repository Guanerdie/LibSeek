from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.pt_sites.avistaz import AvistaZMockAdapter
from app.adapters.pt_sites.catalog import PtSiteCatalog, PtSiteDeclaration
from app.adapters.pt_sites.execution_registry import PtExecutionRegistry
from app.adapters.pt_sites.registry import PtSiteRegistry
from app.api.dependencies import (
    get_operator_principal,
    get_pt_site_catalog,
    get_viewer_principal,
)
from app.core.auth import Principal
from app.db.session import get_session
from app.errors import AppError
from app.main import app
from app.models.entities import (
    ApprovalRequest,
    AuditEvent,
    DownloadExecution,
    DownloadPlan,
    Job,
    MediaItem,
    TorrentCandidateRecord,
    TorrentSearchRun,
)
from app.models.enums import AuthRole, JobStatus, MediaType, WorkflowStatus
from app.schemas.adapters import (
    AdapterManifest,
    PtSearchMode,
    TorrentCandidate,
    TorrentSearchRequest,
)
from app.workers.processor import JobProcessor
from tests.test_pt_site_extensibility import seed_confirmed_media


def site_declaration(
    site_id: str,
    *,
    enabled: bool = True,
    ready: bool = True,
    fetchable: bool = False,
    search_modes: tuple[PtSearchMode, ...] = (PtSearchMode.TEXT,),
) -> PtSiteDeclaration:
    return PtSiteDeclaration(
        site_id=site_id,
        display_name=site_id.replace("-", " ").title(),
        description="Synthetic read-only PT site for offline contract tests",
        search_modes=search_modes,
        media_types=(MediaType.MOVIE, MediaType.TV),
        search_enabled=enabled,
        runtime_ready=ready,
        manual_only=True,
        torrent_fetch_enabled=fetchable,
    )


class SyntheticPtAdapter(AvistaZMockAdapter):
    def __init__(
        self,
        candidate: TorrentCandidate,
        search_calls: list[TorrentSearchRequest],
    ) -> None:
        super().__init__([candidate], manifest_id=candidate.site_id)
        self.search_calls = search_calls

    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            id=self.manifest_id,
            name="Synthetic Two",
            adapter_type="pt_site",
            version="test",
            enabled=True,
            mode="FIXTURE_ONLY",
            description="Pure in-memory synthetic PT adapter",
            capabilities={
                "tmdb_search": False,
                "imdb_search": False,
                "text_search": True,
                "fetch_torrent_enabled": False,
                "write_operations": False,
            },
        )

    async def search(self, request: TorrentSearchRequest) -> list[TorrentCandidate]:
        self.search_calls.append(request)
        return await super().search(request)


@asynccontextmanager
async def api_client_for_catalog(
    session_factory: async_sessionmaker[AsyncSession],
    catalog: PtSiteCatalog,
) -> AsyncIterator[httpx.AsyncClient]:
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    principal = Principal(
        username="pt-catalog-admin",
        role=AuthRole.ADMIN,
        issued_at=0,
        expires_at=2**31,
        csrf_digest="0" * 64,
    )

    async def override_principal() -> Principal:
        return principal

    original_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_pt_site_catalog] = lambda: catalog
    app.dependency_overrides[get_viewer_principal] = override_principal
    app.dependency_overrides[get_operator_principal] = override_principal
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)


def test_catalog_response_is_closed_secret_free_and_has_valid_default() -> None:
    catalog = PtSiteCatalog(
        (
            site_declaration("synthetic-two", fetchable=True),
            site_declaration("disabled-site", enabled=False),
        ),
        default_site_id="synthetic-two",
    )

    response = catalog.public_response().model_dump(mode="json")

    assert response["default_site_id"] == "synthetic-two"
    assert len(response["sites"]) == 2
    ready, disabled = response["sites"]
    assert set(ready) == {
        "site_id",
        "display_name",
        "description",
        "available_for_search",
        "unavailable_reason_code",
        "unavailable_reason_message",
        "mode",
        "search_modes",
        "media_types",
        "manual_only",
        "promotion_metadata",
        "hit_and_run_metadata",
        "torrent_fetch_enabled",
    }
    assert ready["available_for_search"] is True
    assert ready["unavailable_reason_code"] is None
    assert ready["unavailable_reason_message"] is None
    assert ready["torrent_fetch_enabled"] is True
    assert disabled["available_for_search"] is False
    assert disabled["unavailable_reason_code"] == "PT_SITE_SEARCH_DISABLED"
    serialized = str(response).casefold()
    for forbidden in ("cookie", "passkey", "password", "authorization", "https://"):
        assert forbidden not in serialized

    with pytest.raises(ValueError, match="no catalog declaration"):
        PtSiteCatalog((site_declaration("synthetic-two"),), default_site_id="missing")


def test_openapi_exposes_read_only_catalog_and_requires_search_site_id() -> None:
    schema = app.openapi()

    assert schema["info"]["version"] == "0.8.0"
    assert set(schema["paths"]["/api/pt-sites/catalog"]) == {"get"}
    create_search = schema["paths"]["/api/media/{media_id}/torrent-searches"]["post"]
    request_schema = create_search["requestBody"]["content"]["application/json"]["schema"]
    component_name = request_schema["$ref"].rsplit("/", 1)[-1]
    assert "site_id" in schema["components"]["schemas"][component_name]["required"]


def test_catalog_fetch_capability_fails_closed() -> None:
    catalog = PtSiteCatalog(
        (
            site_declaration("search-only"),
            site_declaration("fetchable", fetchable=True),
        )
    )

    with pytest.raises(AppError) as unsupported:
        catalog.require_fetchable("search-only")
    assert unsupported.value.error_code == "PT_SITE_TORRENT_FETCH_UNSUPPORTED"
    assert catalog.require_fetchable("fetchable").site_id == "fetchable"


def test_candidate_internal_reference_fits_download_plan_column() -> None:
    site_id = "s" * 24
    maximum_ref = f"{site_id}:{'d' * 50}:{'r' * 104}"
    assert len(maximum_ref) == 180
    candidate = TorrentCandidate(
        site_id=site_id,
        torrent_id="fixture-id",
        release_title="Fixture release",
        details_ref=maximum_ref,
        media_type=MediaType.MOVIE,
    )
    assert candidate.details_ref == maximum_ref

    with pytest.raises(ValueError, match="at most 180 characters"):
        TorrentCandidate(
            site_id=site_id,
            torrent_id="fixture-id",
            release_title="Fixture release",
            details_ref=f"{site_id}:{'d' * 50}:{'r' * 105}",
            media_type=MediaType.MOVIE,
        )


@pytest.mark.parametrize(
    "update",
    [
        {"torrent_id": "123?passkey=secret"},
        {"details_ref": "synthetic-two:details:cookie=secret"},
        {"details_ref": "other-site:details:safe"},
    ],
)
def test_candidate_identifiers_are_strict_internal_values(
    update: dict[str, str],
) -> None:
    values = {
        "site_id": "synthetic-two",
        "torrent_id": "torrent-123",
        "release_title": "Synthetic Movie 2026",
        "details_ref": "synthetic-two:details:safe-ref",
        "media_type": MediaType.MOVIE,
    }
    values.update(update)

    with pytest.raises(ValueError):
        TorrentCandidate.model_validate(values)


@pytest.mark.asyncio
async def test_registry_rejects_adapter_that_self_declares_disabled() -> None:
    catalog = PtSiteCatalog((site_declaration("synthetic-two"),))
    registry = PtSiteRegistry(catalog)
    registry.register(
        "synthetic-two",
        lambda: AvistaZMockAdapter(manifest_id="synthetic-two"),
    )

    with pytest.raises(AppError) as caught:
        await registry.create("synthetic-two")
    assert caught.value.error_code == "PT_SITE_ADAPTER_DISABLED"


@pytest.mark.asyncio
async def test_catalog_rejects_unknown_disabled_and_not_ready_before_db_writes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    catalog = PtSiteCatalog(
        (
            site_declaration("synthetic-two"),
            site_declaration("disabled-site", enabled=False),
            site_declaration("not-ready", ready=False),
        ),
        default_site_id="synthetic-two",
    )
    async with session_factory() as session:
        media = await seed_confirmed_media(session, source_item_id="catalog-fail-closed")
        initial_workflow_status = media.workflow_status
        initial_updated_at = media.updated_at
        await session.commit()

    async with api_client_for_catalog(session_factory, catalog) as client:
        missing_site_id = await client.post(
            f"/api/media/{media.id}/torrent-searches",
            json={},
        )
        unknown = await client.post(
            f"/api/media/{media.id}/torrent-searches",
            json={"site_id": "unknown-site"},
        )
        disabled = await client.post(
            f"/api/media/{media.id}/torrent-searches",
            json={"site_id": "disabled-site"},
        )
        not_ready = await client.post(
            f"/api/media/{media.id}/torrent-searches",
            json={"site_id": "not-ready"},
        )

    assert missing_site_id.status_code == 422
    assert {
        field["location"] for field in missing_site_id.json()["details"]["fields"]
    } == {"body.site_id"}
    assert unknown.status_code == 409
    assert unknown.json()["error_code"] == "PT_SITE_NOT_REGISTERED"
    assert disabled.status_code == 409
    assert disabled.json()["error_code"] == "PT_SITE_SEARCH_DISABLED"
    assert not_ready.status_code == 409
    assert not_ready.json()["error_code"] == "PT_SITE_NOT_CONFIGURED"
    async with session_factory() as session:
        assert await session.scalar(select(func.count(TorrentSearchRun.id))) == 0
        assert await session.scalar(select(func.count(Job.id))) == 0
        assert await session.scalar(select(func.count(AuditEvent.id))) == 0
        unchanged_media = await session.get(MediaItem, media.id)
        assert unchanged_media is not None
        assert unchanged_media.workflow_status == initial_workflow_status
        assert unchanged_media.updated_at.replace(tzinfo=None) == initial_updated_at.replace(
            tzinfo=None
        )


@pytest.mark.asyncio
async def test_synthetic_second_site_runs_api_to_worker_without_avistaz_fallback(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    catalog = PtSiteCatalog(
        (
            site_declaration(
                "avistaz",
                search_modes=(
                    PtSearchMode.TMDB_ID,
                    PtSearchMode.IMDB_ID,
                    PtSearchMode.TEXT,
                ),
            ),
            site_declaration("synthetic-two", search_modes=(PtSearchMode.TEXT,)),
        ),
        default_site_id="synthetic-two",
    )
    stored_id: str
    async with session_factory() as session:
        media = await seed_confirmed_media(
            session,
            source_item_id="synthetic-two-e2e",
            tmdb_id=123,
        )
        await session.commit()

    async with api_client_for_catalog(session_factory, catalog) as client:
        directory = await client.get("/api/pt-sites/catalog")
        accepted = await client.post(
            f"/api/media/{media.id}/torrent-searches",
            json={"site_id": "synthetic-two"},
        )

    assert directory.status_code == 200
    assert directory.json()["default_site_id"] == "synthetic-two"
    assert {site["site_id"] for site in directory.json()["sites"]} == {
        "avistaz",
        "synthetic-two",
    }
    assert accepted.status_code == 202, accepted.text
    accepted_data = accepted.json()
    assert accepted_data["site_id"] == "synthetic-two"

    candidate = TorrentCandidate(
        site_id="synthetic-two",
        torrent_id="synthetic-torrent-1",
        release_title="Fixture Movie 2026 1080p WEB-DL",
        details_ref="synthetic-two:details:fixture-1",
        media_type=MediaType.MOVIE,
        tmdb_id=123,
        imdb_id="tt0000123",
        year=2026,
        resolution="1080p",
        source="WEB-DL",
        seeders=8,
        hit_and_run=False,
    )
    search_calls: list[TorrentSearchRequest] = []
    avistaz_factory_calls = 0

    def forbidden_avistaz_factory() -> AvistaZMockAdapter:
        nonlocal avistaz_factory_calls
        avistaz_factory_calls += 1
        raise AssertionError("synthetic site search fell back to AvistaZ")

    registry = PtSiteRegistry(catalog)
    registry.register("avistaz", forbidden_avistaz_factory)
    registry.register(
        "synthetic-two",
        lambda: SyntheticPtAdapter(candidate, search_calls),
    )
    registry.assert_complete()
    processor = JobProcessor(
        session_factory,
        "worker-synthetic-two",
        AvistaZMockAdapter,
        pt_site_registry=registry,
    )

    assert await processor.run_once() is True
    assert avistaz_factory_calls == 0
    assert len(search_calls) == 1
    assert search_calls[0].search == "Fixture Movie 2026"
    assert search_calls[0].tmdb is None
    assert search_calls[0].imdb is None

    async with session_factory() as session:
        job = await session.get(Job, accepted_data["job_id"])
        run = await session.get(TorrentSearchRun, accepted_data["id"])
        stored = await session.scalar(
            select(TorrentCandidateRecord).where(
                TorrentCandidateRecord.search_run_id == accepted_data["id"]
            )
        )
        queued = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.entity_id == accepted_data["id"],
                AuditEvent.event_type == "TORRENT_SEARCH_QUEUED",
            )
        )
        assert job is not None
        assert job.status == JobStatus.SUCCEEDED
        assert job.job_type == f"TORRENT_SEARCH:synthetic-two:{media.id}"
        assert job.payload["site_id"] == "synthetic-two"
        assert run is not None
        assert run.site_id == "synthetic-two"
        assert run.status == WorkflowStatus.TORRENT_REVIEW
        assert run.sanitized_request["site_id"] == "synthetic-two"
        assert stored is not None
        assert stored.site_id == "synthetic-two"
        assert stored.candidate_snapshot["site_id"] == "synthetic-two"
        assert stored.candidate_snapshot["details_ref"].startswith(
            "synthetic-two:details:"
        )
        assert queued is not None
        assert queued.sanitized_details["site_id"] == "synthetic-two"
        stored_id = stored.id

    async with api_client_for_catalog(session_factory, catalog) as client:
        approval_response = await client.post(
            f"/api/candidates/{stored_id}/approval-requests",
            json={"expires_in_minutes": 60},
        )

    assert approval_response.status_code == 201, approval_response.text
    approval_data = approval_response.json()
    assert approval_data["candidate"]["site_id"] == "synthetic-two"
    assert approval_data["status"] == "PENDING"

    async with session_factory() as session:
        approval = await session.get(ApprovalRequest, approval_data["id"])
        assert approval is not None
        assert approval.candidate_snapshot["site_id"] == "synthetic-two"
        assert await session.scalar(select(func.count(DownloadPlan.id))) == 0
        assert await session.scalar(select(func.count(DownloadExecution.id))) == 0

    execution_factory_calls = 0

    def forbidden_execution_factory() -> AvistaZMockAdapter:
        nonlocal execution_factory_calls
        execution_factory_calls += 1
        raise AssertionError("search-only site reached the execution adapter factory")

    execution_registry = PtExecutionRegistry(catalog)
    execution_registry.register("synthetic-two", forbidden_execution_factory)
    with pytest.raises(AppError) as execution_blocked:
        await execution_registry.create("synthetic-two")
    assert execution_blocked.value.error_code == "PT_SITE_TORRENT_FETCH_UNSUPPORTED"
    assert execution_factory_calls == 0
