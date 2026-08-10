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


def adapter() -> AvistaZAdapter:
    return AvistaZAdapter(
        username="test-user",
        password="test-password",
        pid="test-pid",
        min_interval_seconds=0,
        sleep=no_sleep,
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
async def test_default_policy_blocks_real_avistaz_network() -> None:
    avistaz = adapter()
    with respx.mock(assert_all_mocked=True), pytest.raises(AssertionError):
        await avistaz.search(TorrentSearchRequest(tmdb=123))
    await avistaz.aclose()
