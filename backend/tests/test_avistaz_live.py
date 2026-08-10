from __future__ import annotations

import logging

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
    transport: httpx.AsyncBaseTransport | None = None, *, enable_torrent_fetch: bool = False
) -> AvistaZAdapter:
    return AvistaZAdapter(
        username="test-user",
        password="test-password",
        pid="test-pid",
        min_interval_seconds=0,
        sleep=no_sleep,
        transport=transport,
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
        "torrent_id": 88,
        "release_title": "Jackett Show 2026 S02E01-E03 2160p WEB-DL",
        "movie_tv": {
            "type": "tv",
            "tmdb": 456,
            "imdb": "tt0456",
            "tvdb": 789,
        },
        "video_quality": {"name": "2160p"},
        "media": {"name": "WEB-DL"},
        "format": {"name": "H.265"},
        "audio": ["Japanese"],
        "subtitle": ["Chinese", "English"],
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
        "url": "https://avistaz.to/torrents/88?token=secret-token",
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
    serialized = candidate.model_dump_json().casefold()
    assert "https://avistaz.to/download" not in serialized
    assert "tracker.invalid" not in serialized
    assert "passkey" not in serialized
    assert "test-pid" not in serialized
    await avistaz.aclose()


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
    assert candidate.tmdb_id == 456
    assert candidate.imdb_id == "tt0456"
    assert candidate.media_type == MediaType.TV
    assert candidate.season == 2
    assert candidate.episodes == [1, 2, 3]
    assert candidate.resolution == "2160p"
    assert candidate.source == "WEB-DL"
    assert candidate.codec == "H.265"
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
