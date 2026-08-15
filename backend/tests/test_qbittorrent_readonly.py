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


def torrent_payload(index: int) -> dict[str, object]:
    return {
        "hash": f"{index:040x}",
        "name": f"Release {index}",
        "size": 2048 + index,
        "progress": 1,
        "ratio": 1,
        "state": "uploading",
        "added_on": 1_700_000_000 + index,
    }


def adapter(
    transport: httpx.AsyncBaseTransport, *, max_response_bytes: int = 10 * 1024 * 1024
) -> QbittorrentReadOnlyAdapter:
    return QbittorrentReadOnlyAdapter(
        base_url=BASE,
        username="runtime-reader",
        password="runtime-password",
        allowed_hosts=("qb.example.test",),
        transport=transport,
        max_response_bytes=max_response_bytes,
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
async def test_response_body_size_is_limited_while_streaming() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            content=b"Ok." + (b"x" * 32),
            headers={"Set-Cookie": "SID=must-not-survive; HttpOnly; Path=/"},
        )
    )
    client = adapter(transport, max_response_bytes=8)
    try:
        with pytest.raises(AppError) as caught:
            await client.authenticate()
    finally:
        await client.aclose()

    assert caught.value.error_code == "QB_RESPONSE_TOO_LARGE"
    assert client._authenticated is False
    assert list(client._client.cookies.items()) == []


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


@pytest.mark.asyncio
async def test_torrent_list_uses_stable_overlapping_pages() -> None:
    source = [torrent_payload(index) for index in range(7)]
    observed_queries: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/auth/login":
            return login_ok()
        assert request.url.path == "/api/v2/torrents/info"
        query = dict(request.url.params)
        observed_queries.append(query)
        assert query["sort"] == "hash"
        assert query["reverse"] == "false"
        offset = int(query["offset"])
        limit = int(query["limit"])
        return httpx.Response(200, json=source[offset : offset + limit])

    client = adapter(httpx.MockTransport(handler))
    client._TORRENT_PAGE_SIZE = 3
    try:
        await client.authenticate()
        torrents = await client.list_torrents()
    finally:
        await client.aclose()

    assert [item.hash for item in torrents] == [f"{index:040x}" for index in range(7)]
    assert [int(query["offset"]) for query in observed_queries] == [0, 2, 4, 6]
    assert all(int(query["limit"]) == 3 for query in observed_queries)


@pytest.mark.asyncio
async def test_torrent_list_retries_with_smaller_pages_when_a_page_is_too_large() -> None:
    source = [torrent_payload(index) for index in range(5)]
    observed_limits: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/auth/login":
            return login_ok()
        limit = int(request.url.params["limit"])
        observed_limits.append(limit)
        if limit > 2:
            return httpx.Response(200, content=b"x" * 1001)
        offset = int(request.url.params["offset"])
        return httpx.Response(200, json=source[offset : offset + limit])

    client = adapter(httpx.MockTransport(handler), max_response_bytes=1000)
    client._TORRENT_PAGE_SIZE = 4
    try:
        await client.authenticate()
        torrents = await client.list_torrents()
    finally:
        await client.aclose()

    assert [item.hash for item in torrents] == [f"{index:040x}" for index in range(5)]
    assert observed_limits == [4, 2, 2, 2, 2, 2]


@pytest.mark.asyncio
async def test_torrent_list_fails_closed_when_page_boundary_changes() -> None:
    source = [torrent_payload(index) for index in range(4)]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/auth/login":
            return login_ok()
        if request.url.params["offset"] == "0":
            return httpx.Response(200, json=source[:3])
        return httpx.Response(200, json=source[:3])

    client = adapter(httpx.MockTransport(handler))
    client._TORRENT_PAGE_SIZE = 3
    try:
        await client.authenticate()
        with pytest.raises(AppError) as caught:
            await client.list_torrents()
    finally:
        await client.aclose()

    assert caught.value.error_code == "QB_TORRENT_LIST_CHANGED"
    assert caught.value.retryable is True


@pytest.mark.asyncio
async def test_torrent_list_rejects_unsorted_or_duplicate_pages() -> None:
    source = [torrent_payload(2), torrent_payload(1)]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/auth/login":
            return login_ok()
        return httpx.Response(200, json=source)

    client = adapter(httpx.MockTransport(handler))
    client._TORRENT_PAGE_SIZE = 3
    try:
        await client.authenticate()
        with pytest.raises(AppError) as caught:
            await client.list_torrents()
    finally:
        await client.aclose()

    assert caught.value.error_code == "QB_TORRENT_LIST_CHANGED"


@pytest.mark.asyncio
async def test_torrent_list_never_returns_a_truncated_safety_limit_result() -> None:
    source = [torrent_payload(index) for index in range(5)]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/auth/login":
            return login_ok()
        offset = int(request.url.params["offset"])
        limit = int(request.url.params["limit"])
        return httpx.Response(200, json=source[offset : offset + limit])

    client = adapter(httpx.MockTransport(handler))
    client._TORRENT_PAGE_SIZE = 3
    client._TORRENT_MAX_ITEMS = 4
    try:
        await client.authenticate()
        with pytest.raises(AppError) as caught:
            await client.list_torrents()
    finally:
        await client.aclose()

    assert caught.value.error_code == "QB_TORRENT_LIST_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_find_torrents_by_hashes_uses_bounded_exact_query_batches() -> None:
    source = {f"{index:040x}": torrent_payload(index) for index in range(1, 5)}
    observed_queries: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/auth/login":
            return login_ok()
        query = dict(request.url.params)
        observed_queries.append(query)
        requested = query["hashes"].split("|")
        return httpx.Response(200, json=[source[value] for value in requested])

    client = adapter(httpx.MockTransport(handler))
    client._HASH_QUERY_BATCH_SIZE = 2
    try:
        await client.authenticate()
        torrents = await client.find_torrents_by_hashes(
            [f"{index:040X}" for index in (3, 1, 2, 1)]
        )
    finally:
        await client.aclose()

    assert {torrent.hash for torrent in torrents} == {
        f"{index:040x}" for index in (1, 2, 3)
    }
    assert [query["hashes"] for query in observed_queries] == [
        f"{1:040x}|{2:040x}",
        f"{3:040x}",
    ]
    assert [query["limit"] for query in observed_queries] == ["3", "2"]
    assert all("offset" not in query for query in observed_queries)


@pytest.mark.asyncio
async def test_find_torrents_by_hashes_rejects_an_ignored_filter() -> None:
    unrelated = torrent_payload(99)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/auth/login":
            return login_ok()
        return httpx.Response(200, json=[unrelated])

    client = adapter(httpx.MockTransport(handler))
    try:
        await client.authenticate()
        with pytest.raises(AppError) as caught:
            await client.find_torrents_by_hashes([INFO_HASH])
    finally:
        await client.aclose()

    assert caught.value.error_code == "QB_TORRENT_HASH_QUERY_INVALID"


@pytest.mark.asyncio
async def test_find_torrents_by_hashes_queries_v2_truncated_alias() -> None:
    v2_hash = "a" * 64
    truncated = v2_hash[:40]
    observed_hashes = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_hashes
        if request.url.path == "/api/v2/auth/login":
            return login_ok()
        observed_hashes = request.url.params["hashes"]
        payload = torrent_payload(1)
        payload["hash"] = truncated
        payload["infohash_v2"] = v2_hash
        return httpx.Response(200, json=[payload])

    client = adapter(httpx.MockTransport(handler))
    try:
        await client.authenticate()
        torrents = await client.find_torrents_by_hashes([v2_hash])
    finally:
        await client.aclose()

    assert observed_hashes == f"{truncated}|{v2_hash}"
    assert torrents[0].infohash_v2 == v2_hash


@pytest.mark.asyncio
async def test_list_recent_torrents_is_bounded_and_sorted_by_added_time() -> None:
    source = [torrent_payload(index) for index in (4, 3, 2)]
    observed_query: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_query
        if request.url.path == "/api/v2/auth/login":
            return login_ok()
        observed_query = dict(request.url.params)
        return httpx.Response(200, json=source)

    client = adapter(httpx.MockTransport(handler))
    try:
        await client.authenticate()
        torrents = await client.list_recent_torrents(3)
    finally:
        await client.aclose()

    assert [torrent.hash for torrent in torrents] == [
        f"{index:040x}" for index in (4, 3, 2)
    ]
    assert observed_query == {
        "sort": "added_on",
        "reverse": "true",
        "limit": "3",
        "offset": "0",
    }


@pytest.mark.asyncio
async def test_has_active_seeding_stops_after_fastest_upload_probe_matches() -> None:
    active = torrent_payload(1)
    active["upspeed"] = 128
    observed_queries: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/auth/login":
            return login_ok()
        query = dict(request.url.params)
        observed_queries.append(query)
        return httpx.Response(200, json=[active])

    client = adapter(httpx.MockTransport(handler))
    try:
        await client.authenticate()
        assert await client.has_active_seeding() is True
    finally:
        await client.aclose()

    assert observed_queries == [
        {
            "filter": "seeding",
            "sort": "upspeed",
            "reverse": "true",
            "limit": "1",
            "offset": "0",
        }
    ]


@pytest.mark.asyncio
async def test_has_active_seeding_probes_seeding_state_extremes() -> None:
    inactive = torrent_payload(1)
    inactive["state"] = "stalledUP"
    inactive["upspeed"] = 0
    uploading = torrent_payload(2)
    uploading["state"] = "uploading"
    uploading["upspeed"] = 0
    observed_queries: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/auth/login":
            return login_ok()
        query = dict(request.url.params)
        observed_queries.append(query)
        if query["sort"] == "upspeed":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[uploading])

    client = adapter(httpx.MockTransport(handler))
    try:
        await client.authenticate()
        assert await client.has_active_seeding() is True
    finally:
        await client.aclose()

    assert observed_queries == [
        {
            "filter": "seeding",
            "sort": "upspeed",
            "reverse": "true",
            "limit": "1",
            "offset": "0",
        },
        {
            "filter": "seeding",
            "sort": "state",
            "reverse": "true",
            "limit": "1",
            "offset": "0",
        },
    ]


@pytest.mark.asyncio
async def test_has_active_seeding_finds_forced_up_after_checking_tasks() -> None:
    checking = torrent_payload(1)
    checking["state"] = "checkingUP"
    checking["upspeed"] = 0
    forced = torrent_payload(2)
    forced["state"] = "forcedUP"
    forced["upspeed"] = 0

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/auth/login":
            return login_ok()
        query = dict(request.url.params)
        if query["sort"] == "upspeed":
            return httpx.Response(200, json=[checking])
        if query["reverse"] == "true":
            return httpx.Response(200, json=[forced])
        return httpx.Response(200, json=[checking, forced])

    client = adapter(httpx.MockTransport(handler))
    client._FORCED_UP_PAGE_SIZE = 3
    try:
        await client.authenticate()
        assert await client.has_active_seeding() is True
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_has_active_seeding_fails_when_forced_up_probe_is_truncated() -> None:
    checking = [torrent_payload(index) for index in range(4)]
    for item in checking:
        item["state"] = "checkingUP"
        item["upspeed"] = 0

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/auth/login":
            return login_ok()
        query = dict(request.url.params)
        if query["sort"] == "upspeed" or query["reverse"] == "true":
            return httpx.Response(200, json=[checking[0]])
        offset = int(query["offset"])
        limit = int(query["limit"])
        return httpx.Response(200, json=checking[offset : offset + limit])

    client = adapter(httpx.MockTransport(handler))
    client._FORCED_UP_PAGE_SIZE = 2
    client._FORCED_UP_MAX_ITEMS = 3
    try:
        await client.authenticate()
        with pytest.raises(AppError) as caught:
            await client.has_active_seeding()
    finally:
        await client.aclose()

    assert caught.value.error_code == "QB_ACTIVE_SEEDING_QUERY_LIMIT"


@pytest.mark.asyncio
async def test_has_active_seeding_fails_closed_when_page_boundary_changes() -> None:
    checking = [torrent_payload(index) for index in range(4)]
    for item in checking:
        item["state"] = "checkingUP"
        item["upspeed"] = 0

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/auth/login":
            return login_ok()
        query = dict(request.url.params)
        if query["sort"] == "upspeed" or query["reverse"] == "true":
            return httpx.Response(200, json=[checking[0]])
        if query["offset"] == "0":
            return httpx.Response(200, json=checking[:2])
        return httpx.Response(200, json=checking[2:])

    client = adapter(httpx.MockTransport(handler))
    client._FORCED_UP_PAGE_SIZE = 2
    try:
        await client.authenticate()
        with pytest.raises(AppError) as caught:
            await client.has_active_seeding()
    finally:
        await client.aclose()

    assert caught.value.error_code == "QB_ACTIVE_SEEDING_QUERY_CHANGED"
    assert caught.value.retryable is True


@pytest.mark.asyncio
async def test_has_active_seeding_rejects_an_ignored_seeding_filter() -> None:
    incomplete = torrent_payload(1)
    incomplete["progress"] = 0.5
    incomplete["state"] = "downloading"
    incomplete["upspeed"] = 0

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/auth/login":
            return login_ok()
        if request.url.params["sort"] == "upspeed":
            return httpx.Response(200, json=[incomplete])
        return httpx.Response(200, json=[incomplete])

    client = adapter(httpx.MockTransport(handler))
    try:
        await client.authenticate()
        with pytest.raises(AppError) as caught:
            await client.has_active_seeding()
    finally:
        await client.aclose()

    assert caught.value.error_code == "QB_ACTIVE_SEEDING_QUERY_INVALID"
