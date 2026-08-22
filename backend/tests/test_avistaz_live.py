from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import httpx
import pytest
import respx

from app.adapters.pt_sites.avistaz_live import AvistaZAdapter
from app.errors import AppError
from app.models.enums import MediaType
from app.schemas.adapters import TorrentSearchRequest

BASE = "https://avistaz.to"


async def no_sleep(_: float) -> None:
    return None


def adapter(
    transport: httpx.AsyncBaseTransport | None = None,
    *,
    enable_torrent_fetch: bool = False,
    before_request: Callable[[], Awaitable[None]] | None = None,
    sleep: Callable[[float], Awaitable[None]] = no_sleep,
) -> AvistaZAdapter:
    return AvistaZAdapter(
        username="test-user",
        password="test-password",
        pid="test-pid",
        min_interval_seconds=0,
        sleep=sleep,
        transport=transport,
        before_request=before_request,
        enable_torrent_fetch=enable_torrent_fetch,
    )


def raw_candidate() -> dict[str, object]:
    return {
        "id": 77,
        "title": "Example Show 2026 S01E03-E05 1080p WEB-DL",
        "type": "tv",
        "tmdb": 123,
        "imdb": "tt0123",
        "resolution": "1080p",
        "source": "WEB-DL",
        "codec": "H.265",
        "audio": ["Japanese"],
        "subtitles": ["Chinese"],
        "size_bytes": 5000,
        "file_count": 3,
        "seeders": 9,
        "leechers": 1,
        "completed": 12,
        "download_factor": 0,
        "hit_and_run": None,
        "download": "https://avistaz.to/download/secret?pid=test-pid",
        "announce": "https://tracker.invalid/announce?passkey=secret",
    }


def jackett_candidate() -> dict[str, object]:
    return {
        "file_name": "Jackett Show 2026 S02E01-E03 2160p WEB-DL",
        "release_title": "Jackett Show",
        "url": "https://avistaz.to/torrents/88?token=secret-token",
        "movie_tv": {
            "tmdb": 456,
            "imdb": "tt0456",
            "tvdb": 789,
        },
        "type": "TV-SHOW",
        "video_quality": "2160p",
        "format": "H.265",
        "audio": [{"id": 1, "language": "Japanese"}],
        "subtitle": [
            {"id": 10, "language": "Chinese"},
            {"id": 11, "language": "English"},
        ],
        "file_size": 9_876_543_210,
        "file_count": 3,
        "seed": 11,
        "leech": 2,
        "completed": 21,
        "download_multiply": 0,
        "upload_multiply": 2,
        "hit_and_run": True,
        "info_hash": "a" * 40,
        "created_at_iso": "2026-08-10T10:20:30Z",
        "download": "https://avistaz.to/download/secret?pid=test-pid",
        "announce": "https://tracker.invalid/announce?passkey=secret-passkey",
    }


@pytest.mark.asyncio
@respx.mock
async def test_auth_success_search_and_download_url_is_discarded() -> None:
    auth = respx.post(f"{BASE}/api/v1/jackett/auth").mock(
        return_value=httpx.Response(200, json={"token": "memory-token"})
    )

    def search(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer memory-token"
        assert request.url.params["tmdb"] == "123"
        assert request.url.params["in"] == "1"
        assert request.url.params["type"] == "2"
        return httpx.Response(200, json={"results": [raw_candidate()]})

    route = respx.get(f"{BASE}/api/v1/jackett/torrents").mock(side_effect=search)
    avistaz = adapter()
    results = await avistaz.search(TorrentSearchRequest(tmdb=123, type=MediaType.TV))
    assert auth.call_count == 1
    assert route.call_count == 1
    assert len(results) == 1
    candidate = results[0]
    assert candidate.season == 1
    assert candidate.episodes == [3, 4, 5]
    assert candidate.details_ref.startswith("avistaz:details:")
    assert candidate.hit_and_run is True
    serialized = candidate.model_dump_json().casefold()
    assert "https://avistaz.to/download" not in serialized
    assert "tracker.invalid" not in serialized
    assert "passkey" not in serialized
    assert "test-pid" not in serialized
    await avistaz.aclose()


@pytest.mark.parametrize(
    ("media_type", "qualities", "expected_type", "expected_qualities"),
    (
        (MediaType.MOVIE, ["2160p", "1080p"], "1", ["6", "3"]),
        (MediaType.TV, ["1080i", "720p", "SD"], "2", ["7", "2", "1"]),
        (None, ["unknown"], None, []),
    ),
)
def test_search_params_use_avistaz_numeric_contract(
    media_type: MediaType | None,
    qualities: list[str],
    expected_type: str | None,
    expected_qualities: list[str],
) -> None:
    params = AvistaZAdapter._search_params(
        TorrentSearchRequest(
            tmdb=123,
            type=media_type,
            limit=100,
            video_quality=qualities,
            subtitle=["Chinese"],
        )
    )

    assert params["in"] == "1"
    assert params.get("type") == expected_type
    assert params["limit"] == 100
    assert params["tmdb"] == 123
    assert params.get("video_quality[]", []) == expected_qualities
    assert "subtitle[]" not in params


@pytest.mark.asyncio
@respx.mock
async def test_search_404_is_an_empty_result() -> None:
    respx.post(f"{BASE}/api/v1/jackett/auth").mock(
        return_value=httpx.Response(200, json={"token": "memory-token"})
    )
    route = respx.get(f"{BASE}/api/v1/jackett/torrents").mock(
        return_value=httpx.Response(404)
    )
    avistaz = adapter()
    try:
        assert await avistaz.search(TorrentSearchRequest(tmdb=123)) == []
    finally:
        await avistaz.aclose()
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    ("status_code", "expected_error_code"),
    ((400, "AVISTAZ_HTTP_ERROR"), (403, "AVISTAZ_HTTP_ERROR"), (500, "AVISTAZ_UNAVAILABLE")),
)
async def test_search_non_404_http_error_is_not_treated_as_empty(
    status_code: int,
    expected_error_code: str,
) -> None:
    respx.post(f"{BASE}/api/v1/jackett/auth").mock(
        return_value=httpx.Response(200, json={"token": "memory-token"})
    )
    respx.get(f"{BASE}/api/v1/jackett/torrents").mock(
        return_value=httpx.Response(status_code)
    )
    avistaz = adapter()
    try:
        with pytest.raises(AppError) as caught:
            await avistaz.search(TorrentSearchRequest(tmdb=123))
    finally:
        await avistaz.aclose()

    assert caught.value.error_code == expected_error_code


@pytest.mark.asyncio
@respx.mock
async def test_jackett_data_envelope_and_real_field_names_are_normalized() -> None:
    respx.post(f"{BASE}/api/v1/jackett/auth").mock(
        return_value=httpx.Response(200, json={"token": "memory-token"})
    )
    respx.get(f"{BASE}/api/v1/jackett/torrents").mock(
        return_value=httpx.Response(200, json={"data": [jackett_candidate()]})
    )

    avistaz = adapter()
    results = await avistaz.search(TorrentSearchRequest(tmdb=456, type=MediaType.TV))
    assert len(results) == 1
    candidate = results[0]
    assert candidate.torrent_id == "88"
    assert candidate.release_title == "Jackett Show 2026 S02E01-E03 2160p WEB-DL"
    assert candidate.tmdb_id == 456
    assert candidate.imdb_id == "tt0456"
    assert candidate.media_type == MediaType.TV
    assert candidate.season == 2
    assert candidate.episodes == [1, 2, 3]
    assert candidate.resolution == "2160p"
    assert candidate.source == "WEB-DL"
    assert candidate.codec == "H.265"
    assert candidate.audio == ["Japanese"]
    assert candidate.subtitles == ["Chinese", "English"]
    assert candidate.size_bytes == 9_876_543_210
    assert candidate.file_count == 3
    assert candidate.seeders == 11
    assert candidate.leechers == 2
    assert candidate.completed == 21
    assert candidate.download_factor == 0
    assert candidate.upload_factor == 2
    assert candidate.hit_and_run is True
    assert candidate.info_hash == "a" * 40
    assert candidate.published_at is not None
    assert candidate.published_at.isoformat() == "2026-08-10T10:20:30+00:00"

    serialized = candidate.model_dump_json().casefold()
    for secret in (
        "https://avistaz.to/download",
        "secret-token",
        "secret-passkey",
        "tracker.invalid",
        "test-pid",
        "announce",
    ):
        assert secret not in serialized
    with pytest.raises(AppError) as caught:
        await avistaz.fetch_torrent(candidate.torrent_id)
    assert caught.value.error_code == "PHASE_NOT_ENABLED"
    await avistaz.aclose()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1.5 GB", 1_500_000_000),
        ("1.5 GiB", 1_610_612_736),
        ("2 MiB", 2_097_152),
        ("3 KB", 3000),
    ],
)
def test_avistaz_human_size_preserves_decimal_and_binary_units(
    value: str,
    expected: int,
) -> None:
    assert AvistaZAdapter._size(value) == expected


@pytest.mark.asyncio
async def test_torrent_fetch_is_explicitly_enabled_and_uses_only_in_memory_url() -> None:
    torrent_bytes = b"d4:infod4:name7:exampleee"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v1/jackett/auth":
            return httpx.Response(200, json={"token": "memory-token"})
        if request.url.path == "/api/v1/jackett/torrents":
            return httpx.Response(200, json={"data": [jackett_candidate()]})
        if request.url.path == "/download/secret":
            assert request.url.params["pid"] == "test-pid"
            return httpx.Response(
                200,
                content=torrent_bytes,
                headers={"Content-Type": "application/x-bittorrent"},
            )
        raise AssertionError(request.url)

    avistaz = adapter(httpx.MockTransport(handler), enable_torrent_fetch=True)
    try:
        candidate = (await avistaz.search(TorrentSearchRequest(tmdb=456)))[0]
        assert await avistaz.fetch_torrent(candidate.torrent_id) == torrent_bytes
        serialized = candidate.model_dump_json().casefold()
        assert "https://avistaz.to/download" not in serialized
        assert "test-pid" not in serialized
    finally:
        await avistaz.aclose()

    assert [request.url.path for request in requests] == [
        "/api/v1/jackett/auth",
        "/api/v1/jackett/torrents",
        "/download/secret",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "upstream_status_code",
        "expected_error_code",
        "expected_status_code",
        "expected_retryable",
    ),
    (
        (403, "AVISTAZ_TORRENT_FETCH_FORBIDDEN", 502, True),
        (400, "AVISTAZ_TORRENT_FETCH_FAILED", 400, False),
        (404, "AVISTAZ_TORRENT_FETCH_FAILED", 400, False),
        (422, "AVISTAZ_TORRENT_FETCH_FAILED", 400, False),
    ),
)
async def test_torrent_fetch_maps_deterministic_4xx_without_sensitive_details(
    upstream_status_code: int,
    expected_error_code: str,
    expected_status_code: int,
    expected_retryable: bool,
) -> None:
    download_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal download_calls
        if request.url.path == "/api/v1/jackett/auth":
            return httpx.Response(200, json={"token": "memory-token"})
        if request.url.path == "/api/v1/jackett/torrents":
            return httpx.Response(200, json={"data": [jackett_candidate()]})
        if request.url.path == "/download/secret":
            download_calls += 1
            return httpx.Response(
                upstream_status_code,
                text="sensitive upstream response must not be retained",
            )
        raise AssertionError(request.url)

    avistaz = adapter(httpx.MockTransport(handler), enable_torrent_fetch=True)
    try:
        candidate = (await avistaz.search(TorrentSearchRequest(tmdb=456)))[0]
        with pytest.raises(AppError) as caught:
            await avistaz.fetch_torrent(candidate.torrent_id)
    finally:
        await avistaz.aclose()

    assert caught.value.error_code == expected_error_code
    assert caught.value.status_code == expected_status_code
    assert caught.value.retryable is expected_retryable
    assert caught.value.details == {"upstream_status_code": upstream_status_code}
    assert "sensitive upstream response" not in repr(caught.value.__dict__)
    assert "test-pid" not in repr(caught.value.__dict__)
    assert download_calls == 1


@pytest.mark.asyncio
@respx.mock
async def test_auth_failure_and_logs_do_not_disclose_credentials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    respx.post(f"{BASE}/api/v1/jackett/auth").mock(return_value=httpx.Response(401))
    avistaz = adapter()
    with caplog.at_level(logging.DEBUG), pytest.raises(AppError) as caught:
        await avistaz.search(TorrentSearchRequest(tmdb=123))
    assert caught.value.error_code == "AVISTAZ_AUTH_FAILED"
    for secret in ("test-user", "test-password", "test-pid"):
        assert secret not in caplog.text
    await avistaz.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", ({"token": ""}, {"access_token": ""}, {"jwt": ""}))
async def test_empty_auth_token_is_rejected(payload: dict[str, str]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/jackett/auth"
        return httpx.Response(200, json=payload)

    avistaz = adapter(httpx.MockTransport(handler))
    try:
        result = await avistaz.probe()
    finally:
        await avistaz.aclose()

    assert result.healthy is False
    assert result.error_code == "AVISTAZ_VALIDATION_ERROR"


@pytest.mark.asyncio
@respx.mock
async def test_expired_token_reauthenticates_and_retries_once() -> None:
    auth_count = 0

    def auth(_: httpx.Request) -> httpx.Response:
        nonlocal auth_count
        auth_count += 1
        return httpx.Response(200, json={"token": f"token-{auth_count}"})

    search_count = 0

    def search(request: httpx.Request) -> httpx.Response:
        nonlocal search_count
        search_count += 1
        if request.headers["authorization"] == "Bearer token-1":
            return httpx.Response(412, json={"message": "expired"})
        return httpx.Response(200, json={"results": []})

    auth_route = respx.post(f"{BASE}/api/v1/jackett/auth").mock(side_effect=auth)
    search_route = respx.get(f"{BASE}/api/v1/jackett/torrents").mock(side_effect=search)
    avistaz = adapter()
    results = await avistaz.search(TorrentSearchRequest(imdb="tt0123"))
    assert results == []
    assert auth_route.call_count == 2
    assert search_route.call_count == 2
    assert auth_count == 2 and search_count == 2
    await avistaz.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_429_uses_bounded_exponential_retry() -> None:
    respx.post(f"{BASE}/api/v1/jackett/auth").mock(
        return_value=httpx.Response(200, json={"token": "memory-token"})
    )
    route = respx.get(f"{BASE}/api/v1/jackett/torrents").mock(
        side_effect=[
            httpx.Response(429, json={"message": "slow down"}),
            httpx.Response(200, json={"results": []}),
        ]
    )
    avistaz = adapter()
    assert await avistaz.search(TorrentSearchRequest(tmdb=123)) == []
    assert route.call_count == 2
    await avistaz.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_429_honors_retry_after_before_retrying() -> None:
    delays: list[float] = []

    async def record_sleep(seconds: float) -> None:
        delays.append(seconds)

    respx.post(f"{BASE}/api/v1/jackett/auth").mock(
        return_value=httpx.Response(200, json={"token": "memory-token"})
    )
    route = respx.get(f"{BASE}/api/v1/jackett/torrents").mock(
        side_effect=[
            httpx.Response(
                429,
                json={"message": "slow down"},
                headers={"Retry-After": "17"},
            ),
            httpx.Response(200, json={"results": []}),
        ]
    )
    avistaz = adapter(sleep=record_sleep)
    assert await avistaz.search(TorrentSearchRequest(tmdb=123)) == []
    assert route.call_count == 2
    assert delays == [17.0]
    await avistaz.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_final_429_preserves_bounded_retry_after() -> None:
    delays: list[float] = []

    async def record_sleep(seconds: float) -> None:
        delays.append(seconds)

    respx.post(f"{BASE}/api/v1/jackett/auth").mock(
        return_value=httpx.Response(200, json={"token": "memory-token"})
    )
    route = respx.get(f"{BASE}/api/v1/jackett/torrents").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "17"}),
            httpx.Response(429, headers={"Retry-After": "23"}),
            httpx.Response(429, headers={"Retry-After": "29"}),
        ]
    )
    avistaz = adapter(sleep=record_sleep)
    try:
        with pytest.raises(AppError) as caught:
            await avistaz.search(TorrentSearchRequest(tmdb=123))
    finally:
        await avistaz.aclose()

    assert route.call_count == 3
    assert delays == [17.0, 23.0]
    assert caught.value.error_code == "AVISTAZ_RATE_LIMITED"
    assert caught.value.retryable is True
    assert caught.value.details == {"retry_after_seconds": 29.0}


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        (None, None),
        ("not-a-retry-delay", None),
        ("-5", 0.0),
        ("nan", None),
        ("inf", None),
        ("-inf", None),
        ("3601", 3600.0),
        ("Thu, 01 Jan 1970 00:00:00 GMT", 0.0),
        ("Fri, 31 Dec 9999 23:59:59 GMT", 3600.0),
    ),
)
def test_retry_after_seconds_rejects_invalid_values_and_applies_safe_bounds(
    value: str | None,
    expected: float | None,
) -> None:
    assert AvistaZAdapter._retry_after_seconds(value) == expected


@pytest.mark.asyncio
async def test_guard_change_after_auth_blocks_search_http_request() -> None:
    requests: list[str] = []
    guard_calls = 0

    async def guard() -> None:
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls == 2:
            raise AppError(
                "AUTOMATION_POLICY_REVISION_CHANGED",
                "policy changed",
                status_code=409,
            )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/api/v1/jackett/auth":
            return httpx.Response(200, json={"token": "memory-token"})
        raise AssertionError("search request must be blocked by the guard")

    avistaz = adapter(httpx.MockTransport(handler), before_request=guard)
    try:
        with pytest.raises(AppError) as caught:
            await avistaz.search(TorrentSearchRequest(tmdb=123))
    finally:
        await avistaz.aclose()

    assert caught.value.error_code == "AUTOMATION_POLICY_REVISION_CHANGED"
    assert guard_calls == 2
    assert requests == ["/api/v1/jackett/auth"]


@pytest.mark.asyncio
async def test_guard_change_after_401_blocks_reauthentication_http_request() -> None:
    requests: list[str] = []
    guard_calls = 0

    async def guard() -> None:
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls == 3:
            raise AppError(
                "AUTOMATION_POLICY_REVISION_CHANGED",
                "policy changed",
                status_code=409,
            )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/api/v1/jackett/auth":
            return httpx.Response(200, json={"token": "memory-token"})
        if request.url.path == "/api/v1/jackett/torrents":
            return httpx.Response(401, json={"message": "expired"})
        raise AssertionError(request.url)

    avistaz = adapter(httpx.MockTransport(handler), before_request=guard)
    try:
        with pytest.raises(AppError) as caught:
            await avistaz.search(TorrentSearchRequest(tmdb=123))
    finally:
        await avistaz.aclose()

    assert caught.value.error_code == "AUTOMATION_POLICY_REVISION_CHANGED"
    assert guard_calls == 3
    assert requests == [
        "/api/v1/jackett/auth",
        "/api/v1/jackett/torrents",
    ]


@pytest.mark.asyncio
async def test_guard_change_after_429_blocks_retry_http_request() -> None:
    requests: list[str] = []
    guard_calls = 0

    async def guard() -> None:
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls == 3:
            raise AppError(
                "AUTOMATION_POLICY_REVISION_CHANGED",
                "policy changed",
                status_code=409,
            )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/api/v1/jackett/auth":
            return httpx.Response(200, json={"token": "memory-token"})
        if request.url.path == "/api/v1/jackett/torrents":
            return httpx.Response(429, json={"message": "slow down"})
        raise AssertionError(request.url)

    avistaz = adapter(httpx.MockTransport(handler), before_request=guard)
    try:
        with pytest.raises(AppError) as caught:
            await avistaz.search(TorrentSearchRequest(tmdb=123))
    finally:
        await avistaz.aclose()

    assert caught.value.error_code == "AUTOMATION_POLICY_REVISION_CHANGED"
    assert guard_calls == 3
    assert requests == [
        "/api/v1/jackett/auth",
        "/api/v1/jackett/torrents",
    ]


@pytest.mark.asyncio
async def test_guard_runs_again_before_following_same_origin_redirect() -> None:
    requests: list[str] = []
    guard_calls = 0

    async def guard() -> None:
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls == 3:
            raise AppError(
                "AUTOMATION_POLICY_REVISION_CHANGED",
                "policy changed",
                status_code=409,
            )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/api/v1/jackett/auth":
            return httpx.Response(200, json={"token": "memory-token"})
        if request.url.path == "/api/v1/jackett/torrents":
            return httpx.Response(
                307,
                headers={"Location": "/api/v1/jackett/redirected-torrents"},
            )
        raise AssertionError("redirected request must be blocked by the guard")

    avistaz = adapter(httpx.MockTransport(handler), before_request=guard)
    try:
        with pytest.raises(AppError) as caught:
            await avistaz.search(TorrentSearchRequest(tmdb=123))
    finally:
        await avistaz.aclose()

    assert caught.value.error_code == "AUTOMATION_POLICY_REVISION_CHANGED"
    assert guard_calls == 3
    assert requests == [
        "/api/v1/jackett/auth",
        "/api/v1/jackett/torrents",
    ]


@pytest.mark.asyncio
@respx.mock
async def test_redirect_outside_avistaz_is_rejected() -> None:
    respx.post(f"{BASE}/api/v1/jackett/auth").mock(
        return_value=httpx.Response(307, headers={"location": "https://evil.invalid/auth"})
    )
    avistaz = adapter()
    with pytest.raises(AppError) as caught:
        await avistaz.search(TorrentSearchRequest(tmdb=123))
    assert caught.value.error_code == "EXTERNAL_HOST_NOT_ALLOWED"
    await avistaz.aclose()


@pytest.mark.asyncio
async def test_authorization_header_is_not_forwarded_to_another_origin() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v1/jackett/auth":
            return httpx.Response(200, json={"token": "memory-token"})
        return httpx.Response(
            307,
            headers={"Location": "https://avistaz.to:444/credential-target"},
        )

    client = adapter(httpx.MockTransport(handler))
    try:
        with pytest.raises(AppError) as caught:
            await client.search(TorrentSearchRequest(search="Example"))
    finally:
        await client.aclose()

    assert caught.value.error_code == "UPSTREAM_CROSS_ORIGIN_REDIRECT"
    assert [request.url.path for request in requests] == [
        "/api/v1/jackett/auth",
        "/api/v1/jackett/torrents",
    ]
@pytest.mark.asyncio
async def test_default_policy_blocks_real_avistaz_network() -> None:
    avistaz = adapter()
    with respx.mock(assert_all_mocked=True), pytest.raises(AssertionError):
        await avistaz.search(TorrentSearchRequest(tmdb=123))
    await avistaz.aclose()
