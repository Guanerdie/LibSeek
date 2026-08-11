from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.pt_sites import AvistaZMockAdapter
from app.adapters.pt_sites.nexusphp import NexusPhpAdapter, NexusPhpHtmlParser
from app.adapters.pt_sites.profiles import NexusPhpSelectors, NexusPhpSiteProfile
from app.adapters.pt_sites.registry import PtSiteRegistry, default_pt_site_registry
from app.errors import AppError
from app.models.entities import AuditEvent, IdentityReview, Job, MediaItem, MetadataMatch
from app.models.enums import (
    IdentityConfidence,
    JobStatus,
    MediaType,
    MetadataStatus,
    WorkflowStatus,
)
from app.schemas.adapters import MetadataRecord, TorrentSearchRequest
from app.schemas.entities import TorrentSearchCreateRequest
from app.services.workflow import enqueue_torrent_search, refresh_media_search_workflow_status
from app.workers.processor import JobProcessor

FIXTURES = Path(__file__).parent / "fixtures" / "nexusphp"


def profile(*, enabled: bool = False) -> NexusPhpSiteProfile:
    return NexusPhpSiteProfile(
        site_id="fixture-nexus",
        display_name="Fixture NexusPHP",
        enabled=enabled,
        base_url="https://tracker.example.invalid",
        search_path="/torrents.php",
        download_path="/download.php?id={torrent_id}&passkey={passkey}",
        category_mapping={MediaType.MOVIE: ("401",), MediaType.TV: ("402",)},
        category_media_types={"movie": MediaType.MOVIE, "tv": MediaType.TV},
        selectors=NexusPhpSelectors(
            row="#torrents tr.torrent",
            details_link="a.details",
            title=".title",
            size=".size",
            seeders=".seeders",
            leechers=".leechers",
            completed=".completed",
            category=".category",
            published_at=".published",
            discount=".discount",
        ),
        discount_text_factors={"FREE": 0.0, "50%": 0.5},
    )


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def recording_gate(
    calls: list[str],
) -> Callable[[str], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def gate(operation: str) -> AsyncIterator[None]:
        calls.append(operation)
        yield

    return gate


async def public_resolver(host: str, port: int) -> tuple[str, ...]:
    del host, port
    return ("93.184.216.34",)


def metadata_record(tmdb_id: int = 123) -> MetadataRecord:
    return MetadataRecord(
        tmdb_id=tmdb_id,
        imdb_id=f"tt{tmdb_id:07d}",
        media_type=MediaType.MOVIE,
        title="Fixture Movie",
        english_title="Fixture Movie",
        year=2026,
        confidence=1,
    )


async def seed_confirmed_media(
    session: AsyncSession,
    *,
    source_item_id: str = "pt-extensibility-fixture",
    tmdb_id: int = 123,
) -> MediaItem:
    now = datetime.now(UTC)
    media = MediaItem(
        source="nextfind",
        source_item_id=source_item_id,
        media_type=MediaType.MOVIE,
        tmdb_id=tmdb_id,
        title="Fixture Movie",
        year=2026,
        identity_confidence=IdentityConfidence.HIGH,
        metadata_status=MetadataStatus.RESOLVED,
        workflow_status=WorkflowStatus.IDENTITY_CONFIRMED,
        discovered_at=now,
        updated_at=now,
    )
    session.add(media)
    await session.flush()
    candidate = metadata_record(tmdb_id)
    match = MetadataMatch(
        media_id=media.id,
        tmdb_id=candidate.tmdb_id,
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
            media_id=media.id,
            metadata_match_id=match.id,
            status="CONFIRMED",
            confirmed_by="fixture-operator",
            candidate_snapshot=candidate.model_dump(mode="json"),
        )
    )
    await session.flush()
    return media


def test_search_request_defaults_to_avistaz_and_rejects_ambiguous_site_ids() -> None:
    assert TorrentSearchCreateRequest().site_id == "avistaz"
    for invalid in ("AvistaZ", "site.example", " site", "site_1", "a" * 25):
        with pytest.raises(ValidationError):
            TorrentSearchCreateRequest(site_id=invalid)
    with pytest.raises(ValidationError):
        TorrentSearchCreateRequest(unexpected="value")


def test_profile_is_secret_free_https_and_same_origin_only() -> None:
    declared = profile()
    serialized = declared.model_dump_json().casefold()
    for forbidden in ("actual-cookie-value", "actual-passkey-value", "password-value"):
        assert forbidden not in serialized
    with pytest.raises(ValidationError):
        NexusPhpSiteProfile.model_validate({**declared.model_dump(), "cookie": "secret"})
    with pytest.raises(ValidationError):
        NexusPhpSiteProfile.model_validate(
            {**declared.model_dump(), "base_url": "http://tracker.example.invalid"}
        )
    with pytest.raises(ValidationError):
        NexusPhpSiteProfile.model_validate(
            {**declared.model_dump(), "search_path": "//other.example/torrents.php"}
        )


@pytest.mark.parametrize(
    "base_url",
    (
        "https://localhost",
        "https://tracker.localhost",
        "https://127.0.0.1",
        "https://10.0.0.1",
        "https://169.254.1.1",
        "https://8.8.8.8",
        "https://[::1]",
        "https://[fe80::1]",
    ),
)
def test_profile_rejects_local_private_and_ip_literal_hosts(base_url: str) -> None:
    declared = profile()
    with pytest.raises(ValidationError):
        NexusPhpSiteProfile.model_validate(
            {**declared.model_dump(), "base_url": base_url}
        )


def test_nexusphp_adapter_rejects_non_allowlisted_target_before_credentials_are_sent() -> None:
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, content=fixture("search_results.html"))

    with pytest.raises(AppError) as caught:
        NexusPhpAdapter(
            profile(enabled=True),
            allowed_hosts=("other.example.invalid",),
            cookie_header="session=must-not-be-sent",
            enable_live_search=True,
            transport=httpx.MockTransport(handler),
            address_resolver=public_resolver,
            min_interval_seconds=0,
            request_gate=recording_gate([]),
        )
    assert caught.value.error_code == "NEXUSPHP_HOST_NOT_ALLOWED"
    assert requests == []


@pytest.mark.asyncio
async def test_nexusphp_rejects_private_dns_resolution_before_credentials_are_sent() -> None:
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.headers.get("cookie", ""))
        return httpx.Response(200, content=fixture("search_results.html"))

    async def private_resolver(host: str, port: int) -> tuple[str, ...]:
        assert host == "tracker.example.invalid"
        assert port == 443
        return ("10.0.0.8",)

    adapter = NexusPhpAdapter(
        profile(enabled=True),
        allowed_hosts=("tracker.example.invalid",),
        cookie_header="session=must-not-be-sent",
        enable_live_search=True,
        transport=httpx.MockTransport(handler),
        address_resolver=private_resolver,
        min_interval_seconds=0,
        request_gate=recording_gate([]),
    )
    try:
        with pytest.raises(AppError) as caught:
            await adapter.search(TorrentSearchRequest(search="Fixture"))
        assert caught.value.error_code == "NEXUSPHP_HOST_ADDRESS_NOT_ALLOWED"
        assert requests == []
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_default_registry_contains_only_avistaz_and_profiles_remain_disabled() -> None:
    registry = default_pt_site_registry(
        lambda: AvistaZMockAdapter(manifest_id="avistaz")
    )
    assert registry.registered_site_ids == ("avistaz",)
    assert isinstance(await registry.create("avistaz"), AvistaZMockAdapter)
    with pytest.raises(AppError) as unknown:
        await registry.create("unknown-site")
    assert unknown.value.error_code == "PT_SITE_NOT_REGISTERED"

    registry.register_nexusphp_profile(
        profile(),
        lambda: NexusPhpAdapter(
            profile(), allowed_hosts=("tracker.example.invalid",)
        ),
    )
    with pytest.raises(AppError) as disabled:
        await registry.create("fixture-nexus")
    assert disabled.value.error_code == "NEXUSPHP_SITE_DISABLED"


@pytest.mark.asyncio
async def test_registry_closes_adapter_and_rejects_factory_identity_mismatch() -> None:
    class WrongIdentityAdapter(AvistaZMockAdapter):
        def __init__(self) -> None:
            super().__init__(manifest_id="wrong-site")
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    adapter = WrongIdentityAdapter()
    registry = PtSiteRegistry()
    registry.register("fixture-nexus", lambda: adapter, enabled=True)

    with pytest.raises(AppError) as caught:
        await registry.create("fixture-nexus")

    assert caught.value.error_code == "PT_SITE_ADAPTER_ID_MISMATCH"
    assert caught.value.message == "PT 站点适配器身份与注册项不一致"
    assert caught.value.details == {}
    assert adapter.closed is True


def test_nexusphp_fixture_parser_produces_only_sanitized_candidates() -> None:
    parser = NexusPhpHtmlParser(profile())
    candidates = parser.parse(fixture("search_results.html"), MediaType.MOVIE)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.site_id == "fixture-nexus"
    assert candidate.torrent_id == "12345"
    assert candidate.release_title == "Fixture Movie 2026 1080p WEB-DL"
    assert candidate.size_bytes == int(1.5 * 1024**3)
    assert candidate.seeders == 12
    assert candidate.download_factor == 0
    assert candidate.hit_and_run is None
    assert candidate.warnings == ["HNR_STATUS_UNKNOWN"]
    serialized = candidate.model_dump_json().casefold()
    for forbidden in ("https://", "download.php", "passkey", "cookie", "authorization"):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    ("filename", "error_code"),
    [
        ("login.html", "NEXUSPHP_LOGIN_REQUIRED"),
        ("captcha.html", "NEXUSPHP_CAPTCHA_REQUIRED"),
        ("challenge.html", "NEXUSPHP_CHALLENGE_UNSUPPORTED"),
        ("missing_field.html", "NEXUSPHP_FIELD_MISSING"),
        ("unknown_category.html", "NEXUSPHP_CATEGORY_UNMAPPED"),
    ],
)
def test_nexusphp_fixture_failures_have_stable_errors(filename: str, error_code: str) -> None:
    parser = NexusPhpHtmlParser(profile())
    with pytest.raises(AppError) as caught:
        parser.parse(fixture(filename), MediaType.MOVIE)
    assert caught.value.error_code == error_code
    assert "secret" not in str(caught.value).casefold()


def test_nexusphp_invalid_profile_selector_fails_closed() -> None:
    declared = profile()
    invalid = declared.model_copy(
        update={"selectors": declared.selectors.model_copy(update={"row": "["})}
    )
    with pytest.raises(AppError) as caught:
        NexusPhpHtmlParser(invalid).parse(fixture("search_results.html"), MediaType.MOVIE)
    assert caught.value.error_code == "NEXUSPHP_PROFILE_INVALID_SELECTOR"


def test_nexusphp_live_mode_requires_cross_worker_request_gate() -> None:
    with pytest.raises(AppError) as caught:
        NexusPhpAdapter(
            profile(enabled=True),
            allowed_hosts=("tracker.example.invalid",),
            cookie_header="session=runtime-only",
            enable_live_search=True,
        )
    assert caught.value.error_code == "NEXUSPHP_REQUEST_GATE_REQUIRED"


@pytest.mark.asyncio
async def test_nexusphp_limit_bounds_parsing_and_memory_cache() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            content=fixture("two_results.html"),
            headers={"content-type": "text/html"},
        )

    adapter = NexusPhpAdapter(
        profile(enabled=True),
        allowed_hosts=("tracker.example.invalid",),
        cookie_header="session=runtime-only",
        enable_live_search=True,
        transport=httpx.MockTransport(handler),
        address_resolver=public_resolver,
        min_interval_seconds=0,
        request_gate=recording_gate([]),
    )
    try:
        results = await adapter.search(
            TorrentSearchRequest(search="Fixture", type=MediaType.MOVIE, limit=1)
        )
        assert [candidate.torrent_id for candidate in results] == ["1001"]
        with pytest.raises(AppError) as missing:
            await adapter.get_torrent_details("1002")
        assert missing.value.error_code == "TORRENT_REFERENCE_NOT_IN_SESSION"
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_nexusphp_rate_limit_honors_bounded_retry_after() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(429, headers={"retry-after": "120"})

    adapter = NexusPhpAdapter(
        profile(enabled=True),
        allowed_hosts=("tracker.example.invalid",),
        cookie_header="session=runtime-only",
        enable_live_search=True,
        transport=httpx.MockTransport(handler),
        address_resolver=public_resolver,
        min_interval_seconds=0,
        request_gate=recording_gate([]),
    )
    try:
        with pytest.raises(AppError) as caught:
            await adapter.search(TorrentSearchRequest(search="Fixture"))
        assert caught.value.error_code == "NEXUSPHP_RATE_LIMITED"
        assert caught.value.details == {"retry_after_seconds": 120.0}
        assert JobProcessor._retry_delay_seconds(caught.value, attempts=1) == 120
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_nexusphp_runtime_secrets_are_memory_only_and_not_in_results() -> None:
    cookie = "session=super-secret-cookie; uid=1"
    passkey = "super-secret-passkey"
    seen_cookie: list[str] = []
    gate_calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_cookie.append(request.headers.get("cookie", ""))
        return httpx.Response(
            200,
            content=fixture("search_results.html"),
            headers={"content-type": "text/html; charset=utf-8"},
        )

    adapter = NexusPhpAdapter(
        profile(enabled=True),
        allowed_hosts=("tracker.example.invalid",),
        cookie_header=cookie,
        passkey=passkey,
        enable_live_search=True,
        transport=httpx.MockTransport(handler),
        address_resolver=public_resolver,
        min_interval_seconds=0,
        request_gate=recording_gate(gate_calls),
    )
    try:
        results = await adapter.search(
            TorrentSearchRequest(search="Fixture Movie 2026", type=MediaType.MOVIE)
        )
        state = await adapter.get_account_state()
        serialized = f"{adapter!r}|{state!r}|{results[0].model_dump_json()}"
        assert seen_cookie == [cookie]
        assert gate_calls == ["search"]
        assert cookie not in serialized
        assert passkey not in serialized
        assert "download.php" not in serialized
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_nexusphp_fetch_gate_never_receives_passkey_or_download_path() -> None:
    passkey = "runtime-passkey-must-stay-private"
    gate_calls: list[str] = []
    before_request_calls = 0

    async def before_request() -> None:
        nonlocal before_request_calls
        before_request_calls += 1

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/torrents.php":
            return httpx.Response(
                200,
                content=fixture("search_results.html"),
                headers={"content-type": "text/html"},
            )
        return httpx.Response(
            200,
            content=b"d4:infod4:name4:testee",
            headers={"content-type": "application/x-bittorrent"},
        )

    adapter = NexusPhpAdapter(
        profile(enabled=True),
        allowed_hosts=("tracker.example.invalid",),
        cookie_header="session=runtime-only",
        passkey=passkey,
        enable_live_search=True,
        enable_torrent_fetch=True,
        transport=httpx.MockTransport(handler),
        address_resolver=public_resolver,
        min_interval_seconds=0,
        request_gate=recording_gate(gate_calls),
    )
    adapter.set_before_request_guard(before_request)
    try:
        await adapter.search(TorrentSearchRequest(search="Fixture", type=MediaType.MOVIE))
        torrent = await adapter.fetch_torrent("12345")
        assert torrent.startswith(b"d")
        assert gate_calls == ["search", "torrent_fetch"]
        assert before_request_calls == 2
        assert all(passkey not in operation for operation in gate_calls)
        assert all("download.php" not in operation for operation in gate_calls)
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_nexusphp_rejects_cross_origin_redirect_before_following() -> None:
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://evil.example/steal"})

    adapter = NexusPhpAdapter(
        profile(enabled=True),
        allowed_hosts=("tracker.example.invalid",),
        cookie_header="session=runtime-only",
        enable_live_search=True,
        transport=httpx.MockTransport(handler),
        address_resolver=public_resolver,
        min_interval_seconds=0,
        request_gate=recording_gate([]),
    )
    try:
        with pytest.raises(AppError) as caught:
            await adapter.search(TorrentSearchRequest(search="Fixture"))
        assert caught.value.error_code == "NEXUSPHP_CROSS_ORIGIN_REDIRECT"
        assert len(requests) == 1
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_nexusphp_guard_blocks_redirect_second_hop_before_send() -> None:
    cookie = "session=runtime-secret-must-not-leak"
    requests: list[str] = []
    guard_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "/redirected-search"})

    async def guard() -> None:
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls == 2:
            raise AppError(
                "AUTOMATION_TORRENT_SEARCH_POLICY_CHANGED",
                "自动 PT 搜索策略已变化",
                status_code=409,
            )

    adapter = NexusPhpAdapter(
        profile(enabled=True),
        allowed_hosts=("tracker.example.invalid",),
        cookie_header=cookie,
        enable_live_search=True,
        transport=httpx.MockTransport(handler),
        address_resolver=public_resolver,
        min_interval_seconds=0,
        request_gate=recording_gate([]),
    )
    adapter.set_before_request_guard(guard)
    try:
        with pytest.raises(AppError) as caught:
            await adapter.search(TorrentSearchRequest(search="Fixture"))
        assert caught.value.error_code == "AUTOMATION_TORRENT_SEARCH_POLICY_CHANGED"
        assert caught.value.details == {"external_request_performed": False}
        assert guard_calls == 2
        assert len(requests) == 1
        assert "/torrents.php" in requests[0]
        serialized = f"{caught.value!s}|{caught.value.details!r}"
        assert cookie not in serialized
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_search_jobs_are_idempotent_per_media_and_site(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        media = await seed_confirmed_media(session)
        first_run, first_job, first_deduplicated = await enqueue_torrent_search(
            session,
            media,
            TorrentSearchCreateRequest(site_id="avistaz"),
            max_attempts=3,
        )
        duplicate_run, duplicate_job, duplicate = await enqueue_torrent_search(
            session,
            media,
            TorrentSearchCreateRequest(site_id="avistaz"),
            max_attempts=3,
        )
        other_run, other_job, other_deduplicated = await enqueue_torrent_search(
            session,
            media,
            TorrentSearchCreateRequest(site_id="fixture-nexus"),
            max_attempts=3,
        )
        await session.commit()

        assert first_deduplicated is False
        assert duplicate is True
        assert first_run.id == duplicate_run.id
        assert first_job.id == duplicate_job.id
        assert other_deduplicated is False
        assert other_run.id != first_run.id
        assert first_job.job_type == f"TORRENT_SEARCH:avistaz:{media.id}"
        assert other_job.job_type == f"TORRENT_SEARCH:fixture-nexus:{media.id}"
        assert first_job.payload["site_id"] == "avistaz"
        assert other_job.payload["site_id"] == "fixture-nexus"
        events = list(
            (
                await session.scalars(
                    select(AuditEvent).where(AuditEvent.event_type == "TORRENT_SEARCH_QUEUED")
                )
            ).all()
        )
        assert {event.sanitized_details["site_id"] for event in events} == {
            "avistaz",
            "fixture-nexus",
        }


@pytest.mark.asyncio
async def test_media_search_status_is_aggregated_across_site_runs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        media = await seed_confirmed_media(session)
        avistaz, _, _ = await enqueue_torrent_search(
            session,
            media,
            TorrentSearchCreateRequest(site_id="avistaz"),
            max_attempts=3,
        )
        nexus, _, _ = await enqueue_torrent_search(
            session,
            media,
            TorrentSearchCreateRequest(site_id="fixture-nexus"),
            max_attempts=3,
        )

        avistaz.status = WorkflowStatus.TORRENT_REVIEW
        nexus.status = WorkflowStatus.NO_CANDIDATE
        await refresh_media_search_workflow_status(session, media)
        assert media.workflow_status == WorkflowStatus.TORRENT_REVIEW

        avistaz.status = WorkflowStatus.SEARCH_FAILED
        nexus.status = WorkflowStatus.PT_SEARCHING
        await refresh_media_search_workflow_status(session, media)
        assert media.workflow_status == WorkflowStatus.PT_SEARCHING

        avistaz.status = WorkflowStatus.SEARCH_FAILED
        nexus.status = WorkflowStatus.NO_CANDIDATE
        await refresh_media_search_workflow_status(session, media)
        assert media.workflow_status == WorkflowStatus.NO_CANDIDATE


@pytest.mark.asyncio
async def test_media_status_aggregation_locks_media_before_reading_runs() -> None:
    now = datetime.now(UTC)
    media = MediaItem(
        source="nextfind",
        source_item_id="aggregation-lock-contract",
        media_type=MediaType.MOVIE,
        tmdb_id=456,
        title="Aggregation lock",
        identity_confidence=IdentityConfidence.HIGH,
        metadata_status=MetadataStatus.RESOLVED,
        workflow_status=WorkflowStatus.PT_SEARCHING,
        discovered_at=now,
        updated_at=now,
    )
    media.id = "aggregation-lock-media"
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = media
    scalar_result = MagicMock()
    scalar_result.all.return_value = [WorkflowStatus.TORRENT_REVIEW]
    session.scalars.return_value = scalar_result

    result = await refresh_media_search_workflow_status(session, media)

    assert result == WorkflowStatus.TORRENT_REVIEW
    session.get.assert_awaited_once_with(MediaItem, media.id, with_for_update=True)
    session.scalars.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_safely_processes_legacy_avistaz_job_without_payload_site_id(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        media = await seed_confirmed_media(session)
        run, job, _ = await enqueue_torrent_search(
            session,
            media,
            TorrentSearchCreateRequest(),
            max_attempts=3,
        )
        job.job_type = f"TORRENT_SEARCH:{media.id}"
        job.payload = {key: value for key, value in job.payload.items() if key != "site_id"}
        await session.commit()

    processor = JobProcessor(
        session_factory,
        "worker-legacy-avistaz",
        AvistaZMockAdapter,
        None,
        lambda: AvistaZMockAdapter(manifest_id="avistaz"),
    )
    assert await processor.run_once() is True
    async with session_factory() as session:
        refreshed_job = await session.get(Job, job.id)
        refreshed_run = await session.get(type(run), run.id)
        assert refreshed_job is not None
        assert refreshed_job.status == JobStatus.SUCCEEDED
        assert refreshed_run is not None
        assert refreshed_run.status == WorkflowStatus.NO_CANDIDATE
        assert refreshed_run.error_code is None


@pytest.mark.asyncio
async def test_worker_fails_closed_when_job_media_does_not_match_search_run(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        run_media = await seed_confirmed_media(
            session,
            source_item_id="pt-binding-run-media",
            tmdb_id=123,
        )
        payload_media = await seed_confirmed_media(
            session,
            source_item_id="pt-binding-payload-media",
            tmdb_id=456,
        )
        run, job, _ = await enqueue_torrent_search(
            session,
            run_media,
            TorrentSearchCreateRequest(site_id="avistaz"),
            max_attempts=3,
        )
        job.job_type = f"TORRENT_SEARCH:avistaz:{payload_media.id}"
        job.payload = {**job.payload, "media_id": payload_media.id}
        await session.commit()

    adapter_calls = 0

    def pt_factory() -> AvistaZMockAdapter:
        nonlocal adapter_calls
        adapter_calls += 1
        return AvistaZMockAdapter(manifest_id="avistaz")

    processor = JobProcessor(
        session_factory,
        "worker-cross-media-binding",
        AvistaZMockAdapter,
        None,
        pt_factory,
    )
    assert await processor.run_once() is True
    assert adapter_calls == 0

    async with session_factory() as session:
        refreshed_job = await session.get(Job, job.id)
        refreshed_run = await session.get(type(run), run.id)
        assert refreshed_job is not None
        assert refreshed_job.status == JobStatus.FAILED
        assert refreshed_job.error_code == "PT_MEDIA_BINDING_MISMATCH"
        assert refreshed_run is not None
        assert refreshed_run.status == WorkflowStatus.SEARCH_FAILED
        assert refreshed_run.error_code == "PT_MEDIA_BINDING_MISMATCH"


@pytest.mark.asyncio
async def test_worker_fails_closed_for_unregistered_run_site(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        media = await seed_confirmed_media(session)
        run, job, _ = await enqueue_torrent_search(
            session,
            media,
            TorrentSearchCreateRequest(site_id="fixture-nexus"),
            max_attempts=3,
        )
        await session.commit()

    processor = JobProcessor(
        session_factory,
        "worker-pt-registry",
        AvistaZMockAdapter,
        pt_site_registry=PtSiteRegistry(),
    )
    assert await processor.run_once() is True
    async with session_factory() as session:
        refreshed_job = await session.get(Job, job.id)
        refreshed_run = await session.get(type(run), run.id)
        assert refreshed_job is not None
        assert refreshed_job.status == JobStatus.FAILED
        assert refreshed_job.error_code == "PT_SITE_NOT_REGISTERED"
        assert refreshed_run is not None
        assert refreshed_run.status == WorkflowStatus.SEARCH_FAILED
        assert refreshed_run.error_code == "PT_SITE_NOT_REGISTERED"
        failed = await session.scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "TORRENT_SEARCH_FAILED")
            .order_by(AuditEvent.created_at.desc())
        )
        assert failed is not None
        assert failed.sanitized_details["site_id"] == "fixture-nexus"
