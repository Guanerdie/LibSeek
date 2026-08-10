from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from app.adapters.downloaders.qbittorrent_readonly import QbittorrentReadOnlyAdapter
from app.errors import AppError

BASE = "https://qb.example.test"
INFO_HASH = "0123456789abcdef0123456789abcdef01234567"


def login_ok() -> httpx.Response:
    return httpx.Response(
        200,
        text="Ok.",
        headers={"Set-Cookie": "SID=memory-only-session; HttpOnly; Path=/"},
    )


def adapter(transport: httpx.AsyncBaseTransport) -> QbittorrentReadOnlyAdapter:
    return QbittorrentReadOnlyAdapter(
        base_url=BASE,
        username="runtime-reader",
        password="runtime-password",
        allowed_hosts=("qb.example.test",),
        transport=transport,
    )


@pytest.mark.asyncio
async def test_low_level_request_whitelist_blocks_unapproved_routes_before_transport() -> None:
    transport_calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal transport_calls
        transport_calls += 1
        return httpx.Response(500)

    client = adapter(httpx.MockTransport(handler))
    try:
        for method, path in (
            ("POST", "/api/v2/torrents/add"),
            ("GET", "/api/v2/app/preferences"),
            ("POST", "/api/v2/app/version"),
        ):
            with pytest.raises(AppError) as caught:
                await client._request(method, path, require_auth=False)
            assert caught.value.error_code == "QB_REQUEST_NOT_ALLOWED"
            assert caught.value.status_code == 405
    finally:
        await client.aclose()

    assert transport_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        ("redirect_302", "QB_HTTP_ERROR"),
        ("redirect_307", "QB_HTTP_ERROR"),
        ("html", "QB_RESPONSE_INVALID"),
        ("fails", "QB_AUTH_FAILED"),
        ("missing_sid", "QB_RESPONSE_INVALID"),
        ("timeout", "QB_TIMEOUT"),
        ("network", "QB_NETWORK_ERROR"),
    ],
)
async def test_reauthentication_failure_clears_old_and_new_sessions(
    failure: str,
    expected_code: str,
) -> None:
    login_cookies: list[str | None] = []
    login_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_attempts
        login_attempts += 1
        login_cookies.append(request.headers.get("Cookie"))
        if login_attempts == 1:
            return login_ok()
        if failure == "redirect_302":
            return httpx.Response(302, headers={"Location": "/login"})
        if failure == "redirect_307":
            return httpx.Response(307, headers={"Location": "/login"})
        if failure == "html":
            return httpx.Response(
                200,
                text="<!doctype html><html><body>login</body></html>",
                headers={
                    "Content-Type": "text/html",
                    "Set-Cookie": "SID=rejected-session; HttpOnly; Path=/",
                },
            )
        if failure == "fails":
            return httpx.Response(
                200,
                text="Fails.",
                headers={"Set-Cookie": "SID=rejected-session; HttpOnly; Path=/"},
            )
        if failure == "missing_sid":
            return httpx.Response(200, text="Ok.")
        if failure == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        raise httpx.ConnectError("connection failed", request=request)

    client = adapter(httpx.MockTransport(handler))
    try:
        assert client._client._trust_env is False
        await client.authenticate()
        assert client._authenticated is True
        assert client._client.cookies.get("SID") == "memory-only-session"

        with pytest.raises(AppError) as caught:
            await client.authenticate()
        assert caught.value.error_code == expected_code
        assert client._authenticated is False
        assert list(client._client.cookies.items()) == []

        with pytest.raises(AppError) as unauthenticated:
            await client.get_version()
        assert unauthenticated.value.error_code == "QB_NOT_AUTHENTICATED"
    finally:
        await client.aclose()

    assert login_cookies == [None, None]
    assert login_attempts == 2


@pytest.mark.asyncio
async def test_sid_login_and_all_read_only_resources() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/api/v2/auth/login":
            assert parse_qs(request.content.decode()) == {
                "username": ["runtime-reader"],
                "password": ["runtime-password"],
            }
            return login_ok()
        assert request.headers["Cookie"] == "SID=memory-only-session"
        if request.url.path == "/api/v2/app/version":
            return httpx.Response(200, text="v5.0.4")
        if request.url.path == "/api/v2/app/webapiVersion":
            return httpx.Response(200, text="2.11.4")
        if request.url.path == "/api/v2/torrents/info":
            return httpx.Response(
                200,
                json=[
                    {
                        "hash": INFO_HASH,
                        "name": "Release 2026 1080p",
                        "size": 2048,
                        "progress": 1,
                        "ratio": 2.5,
                        "state": "uploading",
                        "added_on": 1_700_000_000,
                        "completion_on": 1_700_000_100,
                        "seeding_time": 100,
                        "uploaded": 4096,
                        "upspeed": 128,
                        "category": "movies",
                        "tags": "read-only",
                        "save_path": "/downloads/movies",
                    }
                ],
            )
        if request.url.path == "/api/v2/torrents/files":
            assert request.url.params["hash"] == INFO_HASH
            return httpx.Response(
                200,
                json=[
                    {
                        "index": 0,
                        "name": "Release/movie.mkv",
                        "size": 2048,
                        "progress": 1,
                        "priority": 1,
                    }
                ],
            )
        if request.url.path == "/api/v2/torrents/categories":
            return httpx.Response(200, json={"movies": {"savePath": "/downloads/movies"}})
        raise AssertionError(request.url.path)

    client = adapter(httpx.MockTransport(handler))
    try:
        await client.authenticate()
        assert await client.get_version() == "v5.0.4"
        assert await client.get_web_api_version() == "2.11.4"
        assert (await client.list_torrents())[0].ratio == 2.5
        assert (await client.get_torrent_files(INFO_HASH))[0].progress == 1
        assert (await client.get_categories())["movies"].save_path == "/downloads/movies"
    finally:
        await client.aclose()

    assert paths == [
        "/api/v2/auth/login",
        "/api/v2/app/version",
        "/api/v2/app/webapiVersion",
        "/api/v2/torrents/info",
        "/api/v2/torrents/files",
        "/api/v2/torrents/categories",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_login_failure_is_stable_and_secret_free(status: int, caplog) -> None:
    client = adapter(httpx.MockTransport(lambda _request: httpx.Response(status, text="Denied")))
    try:
        with pytest.raises(AppError) as caught:
            await client.authenticate()
    finally:
        await client.aclose()
    assert caught.value.error_code == "QB_AUTH_FAILED"
    assert "runtime-password" not in str(caught.value.details)
    assert "runtime-password" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 307])
async def test_login_redirect_is_qb_http_error(status: int) -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(status, headers={"Location": "/login"})
    )
    client = adapter(transport)
    try:
        with pytest.raises(AppError) as caught:
            await client.authenticate()
    finally:
        await client.aclose()
    assert caught.value.error_code == "QB_HTTP_ERROR"
    assert caught.value.details == {"status_code": status}


@pytest.mark.asyncio
async def test_html_login_page_is_not_success() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            text="<!doctype html><html><body>login</body></html>",
            headers={"Content-Type": "text/html"},
        )
    )
    client = adapter(transport)
    try:
        with pytest.raises(AppError) as caught:
            await client.authenticate()
    finally:
        await client.aclose()
    assert caught.value.error_code == "QB_RESPONSE_INVALID"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("progress", 1.01),
        ("ratio", -1.01),
        ("added_on", 253_402_300_800),
        ("size", -1),
    ],
)
async def test_torrent_numeric_ranges_are_strict(field: str, value: object) -> None:
    payload: dict[str, object] = {
        "hash": INFO_HASH,
        "name": "Unsafe upstream",
        "size": 1,
        "progress": 1,
        "ratio": 1,
        "state": "uploading",
        "added_on": 1_700_000_000,
    }
    payload[field] = value

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/auth/login":
            return login_ok()
        return httpx.Response(200, json=[payload])

    client = adapter(httpx.MockTransport(handler))
    try:
        await client.authenticate()
        with pytest.raises(AppError) as caught:
            await client.list_torrents()
    finally:
        await client.aclose()
    assert caught.value.error_code == "QB_RESPONSE_INVALID"
    assert "Unsafe upstream" not in str(caught.value.details)
