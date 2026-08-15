from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from urllib.parse import parse_qs

import bencodepy  # type: ignore[import-untyped]
import httpx
import pytest

from app.adapters.downloaders.qbittorrent import QbittorrentAdapter
from app.errors import AppError

BASE = "https://qb.example.test"


def torrent_fixture() -> tuple[bytes, str]:
    info = {
        b"length": 20_000,
        b"name": b"Example.mkv",
        b"piece length": 16_384,
        b"pieces": b"p" * 40,
    }
    payload = bencodepy.encode({b"info": info})
    return payload, hashlib.sha1(bencodepy.encode(info)).hexdigest()


def v2_torrent_fixture() -> tuple[bytes, str]:
    info = {
        b"file tree": {
            b"Example.mkv": {
                b"": {b"length": 20_000, b"pieces root": b"r" * 32}
            }
        },
        b"meta version": 2,
        b"name": b"Example.mkv",
        b"piece length": 16_384,
    }
    payload = bencodepy.encode({b"info": info})
    return payload, hashlib.sha256(bencodepy.encode(info)).hexdigest()


def adapter(
    transport: httpx.AsyncBaseTransport,
    *,
    enable_write: bool = True,
    before_request: Callable[[], Awaitable[None]] | None = None,
) -> QbittorrentAdapter:
    return QbittorrentAdapter(
        base_url=BASE,
        username="runtime-user",
        password="runtime-password",
        allowed_hosts=("qb.example.test",),
        transport=transport,
        enable_write=enable_write,
        before_request=before_request,
    )


def login_response() -> httpx.Response:
    return httpx.Response(
        200,
        text="Ok.",
        headers={"Set-Cookie": "SID=memory-session; HttpOnly; Path=/"},
    )


def categories_response(*names: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={name: {"savePath": "/downloads/approved"} for name in names},
    )


@pytest.mark.asyncio
async def test_write_is_disabled_before_any_network_request() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    client = adapter(httpx.MockTransport(handler), enable_write=False)
    torrent, info_hash = torrent_fixture()
    try:
        with pytest.raises(AppError) as caught:
            await client.add_torrent(
                torrent,
                expected_info_hash=info_hash,
                save_path="/downloads",
                category="movies",
            )
    finally:
        await client.aclose()

    assert caught.value.error_code == "QB_WRITE_DISABLED"
    assert calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ("/api/v2/torrents/add", "/api/v2/torrents/createCategory"),
)
async def test_low_level_write_request_cannot_bypass_write_gate(path: str) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text="Ok.")

    client = adapter(httpx.MockTransport(handler), enable_write=False)
    try:
        with pytest.raises(AppError) as caught:
            await client._request(
                "POST",
                path,
                data={"category": "movies"},
            )
    finally:
        await client.aclose()

    assert caught.value.error_code == "QB_WRITE_DISABLED"
    assert calls == 0


@pytest.mark.asyncio
async def test_add_requires_a_persisted_expected_info_hash() -> None:
    client = adapter(httpx.MockTransport(lambda _request: login_response()))
    torrent, _ = torrent_fixture()
    try:
        await client.authenticate()
        with pytest.raises(AppError) as caught:
            await client.add_torrent(
                torrent,
                expected_info_hash="",
                save_path="/downloads",
                category="movies",
            )
    finally:
        await client.aclose()

    assert caught.value.error_code == "QB_EXPECTED_INFO_HASH_REQUIRED"


@pytest.mark.asyncio
async def test_existing_info_hash_is_idempotent_and_skips_add() -> None:
    torrent, info_hash = torrent_fixture()
    paths: list[str] = []
    queried_hashes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/api/v2/auth/login":
            return login_response()
        if request.url.path == "/api/v2/torrents/info":
            queried_hashes.append(request.url.params["hashes"])
            return httpx.Response(
                200,
                json=[
                    {
                        "hash": info_hash,
                        "name": "Example.mkv",
                        "size": 20_000,
                        "progress": 0.5,
                        "ratio": 0,
                        "state": "downloading",
                        "added_on": 1_700_000_000,
                    }
                ],
            )
        raise AssertionError(request.url.path)

    client = adapter(httpx.MockTransport(handler))
    try:
        await client.authenticate()
        result = await client.add_torrent(
            torrent,
            expected_info_hash=info_hash,
            save_path="/downloads",
            category="movies",
        )
    finally:
        await client.aclose()

    assert result.outcome == "ALREADY_PRESENT"
    assert result.info_hash == info_hash
    assert paths == ["/api/v2/auth/login", "/api/v2/torrents/info"]
    assert queried_hashes == [info_hash]


@pytest.mark.asyncio
async def test_existing_v2_torrent_matches_qb_truncated_and_full_hash_aliases() -> None:
    torrent, info_hash_v2 = v2_torrent_fixture()
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/api/v2/auth/login":
            return login_response()
        if request.url.path == "/api/v2/torrents/info":
            return httpx.Response(
                200,
                json=[
                    {
                        "hash": info_hash_v2[:40],
                        "infohash_v1": "",
                        "infohash_v2": info_hash_v2,
                        "name": "Example.mkv",
                        "size": 20_000,
                        "progress": 0.5,
                        "ratio": 0,
                        "state": "downloading",
                        "added_on": 1_700_000_000,
                    }
                ],
            )
        raise AssertionError(request.url.path)

    client = adapter(httpx.MockTransport(handler))
    try:
        await client.authenticate()
        result = await client.add_torrent(
            torrent,
            expected_info_hash=info_hash_v2,
            save_path="/downloads",
            category="movies",
        )
    finally:
        await client.aclose()

    assert result.outcome == "ALREADY_PRESENT"
    assert result.info_hash == info_hash_v2
    assert paths == ["/api/v2/auth/login", "/api/v2/torrents/info"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("web_api_version", "state_field", "other_state_field"),
    [
        ("2.10.99", b"paused", b"stopped"),
        ("2.11.0", b"stopped", b"paused"),
    ],
)
@pytest.mark.parametrize(
    ("start_immediately", "expected_state_value"),
    [
        (False, b"true"),
        (True, b"false"),
    ],
)
async def test_add_uses_version_specific_state_field_and_approved_target(
    web_api_version: str,
    state_field: bytes,
    other_state_field: bytes,
    start_immediately: bool,
    expected_state_value: bytes,
) -> None:
    torrent, info_hash = torrent_fixture()
    add_body = b""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal add_body
        paths.append(request.url.path)
        if request.url.path == "/api/v2/auth/login":
            return login_response()
        if request.url.path == "/api/v2/torrents/info":
            return httpx.Response(200, json=[])
        if request.url.path == "/api/v2/app/webapiVersion":
            return httpx.Response(200, text=web_api_version)
        if request.url.path == "/api/v2/torrents/categories":
            return categories_response("movies")
        if request.url.path == "/api/v2/torrents/add":
            add_body = request.content
            return httpx.Response(200, text="Ok.")
        raise AssertionError(request.url.path)

    client = adapter(httpx.MockTransport(handler))
    try:
        await client.authenticate()
        result = await client.add_torrent(
            torrent,
            expected_info_hash=info_hash,
            save_path="/downloads/approved",
            category="movies",
            tags=("unin", "approved"),
            start_immediately=start_immediately,
        )
    finally:
        await client.aclose()

    assert result.outcome == "SUBMITTED"
    assert torrent in add_body
    for value in (b"/downloads/approved", b"movies", b"unin,approved"):
        assert value in add_body
    assert (
        b'name="' + state_field + b'"\r\n\r\n' + expected_state_value + b"\r\n"
        in add_body
    )
    assert b'name="' + other_state_field + b'"' not in add_body
    assert paths == [
        "/api/v2/auth/login",
        "/api/v2/torrents/info",
        "/api/v2/app/webapiVersion",
        "/api/v2/torrents/categories",
        "/api/v2/torrents/add",
    ]


@pytest.mark.asyncio
async def test_missing_category_is_created_and_verified_before_add() -> None:
    torrent, info_hash = torrent_fixture()
    paths: list[str] = []
    create_form: dict[str, list[str]] = {}
    category_created = False
    category_write_guard_calls = 0
    add_write_guard_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal category_created, create_form
        paths.append(request.url.path)
        if request.url.path == "/api/v2/auth/login":
            return login_response()
        if request.url.path == "/api/v2/torrents/info":
            return httpx.Response(200, json=[])
        if request.url.path == "/api/v2/app/webapiVersion":
            return httpx.Response(200, text="2.11.4")
        if request.url.path == "/api/v2/torrents/categories":
            return categories_response("movies") if category_created else categories_response()
        if request.url.path == "/api/v2/torrents/createCategory":
            create_form = parse_qs(request.content.decode("utf-8"))
            category_created = True
            return httpx.Response(200, text="Ok.")
        if request.url.path == "/api/v2/torrents/add":
            return httpx.Response(200, text="Ok.")
        raise AssertionError(request.url.path)

    async def category_write_guard() -> None:
        nonlocal category_write_guard_calls
        category_write_guard_calls += 1

    async def add_write_guard() -> None:
        nonlocal add_write_guard_calls
        add_write_guard_calls += 1

    client = adapter(httpx.MockTransport(handler))
    try:
        await client.authenticate()
        result = await client.add_torrent(
            torrent,
            expected_info_hash=info_hash,
            save_path="/downloads/approved",
            category="movies",
            category_write_guard=category_write_guard,
            write_guard=add_write_guard,
        )
    finally:
        await client.aclose()

    assert result.outcome == "SUBMITTED"
    assert create_form == {
        "category": ["movies"],
        "savePath": ["/downloads/approved"],
    }
    assert category_write_guard_calls == 1
    assert add_write_guard_calls == 1
    assert paths == [
        "/api/v2/auth/login",
        "/api/v2/torrents/info",
        "/api/v2/app/webapiVersion",
        "/api/v2/torrents/categories",
        "/api/v2/torrents/createCategory",
        "/api/v2/torrents/categories",
        "/api/v2/torrents/add",
    ]


@pytest.mark.asyncio
async def test_category_creation_not_observed_prevents_add() -> None:
    torrent, info_hash = torrent_fixture()
    paths: list[str] = []
    category_write_guard_calls = 0
    add_write_guard_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/api/v2/auth/login":
            return login_response()
        if request.url.path == "/api/v2/torrents/info":
            return httpx.Response(200, json=[])
        if request.url.path == "/api/v2/app/webapiVersion":
            return httpx.Response(200, text="2.11.4")
        if request.url.path == "/api/v2/torrents/categories":
            return categories_response()
        if request.url.path == "/api/v2/torrents/createCategory":
            return httpx.Response(200, text="Ok.")
        if request.url.path == "/api/v2/torrents/add":
            raise AssertionError("unverified category must prevent torrent submission")
        raise AssertionError(request.url.path)

    async def category_write_guard() -> None:
        nonlocal category_write_guard_calls
        category_write_guard_calls += 1

    async def add_write_guard() -> None:
        nonlocal add_write_guard_calls
        add_write_guard_calls += 1

    client = adapter(httpx.MockTransport(handler))
    try:
        await client.authenticate()
        with pytest.raises(AppError) as caught:
            await client.add_torrent(
                torrent,
                expected_info_hash=info_hash,
                save_path="/downloads/approved",
                category="movies",
                category_write_guard=category_write_guard,
                write_guard=add_write_guard,
            )
    finally:
        await client.aclose()

    assert caught.value.error_code == "QB_CATEGORY_CREATE_FAILED"
    assert caught.value.retryable is True
    assert caught.value.details == {"category": "movies"}
    assert category_write_guard_calls == 1
    assert add_write_guard_calls == 0
    assert paths == [
        "/api/v2/auth/login",
        "/api/v2/torrents/info",
        "/api/v2/app/webapiVersion",
        "/api/v2/torrents/categories",
        "/api/v2/torrents/createCategory",
        "/api/v2/torrents/categories",
    ]


@pytest.mark.asyncio
async def test_add_transport_failure_after_category_creation_is_outcome_unknown() -> None:
    torrent, info_hash = torrent_fixture()
    paths: list[str] = []
    category_created = False
    category_write_guard_calls = 0
    add_write_guard_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal category_created
        paths.append(request.url.path)
        if request.url.path == "/api/v2/auth/login":
            return login_response()
        if request.url.path == "/api/v2/torrents/info":
            return httpx.Response(200, json=[])
        if request.url.path == "/api/v2/app/webapiVersion":
            return httpx.Response(200, text="2.11.4")
        if request.url.path == "/api/v2/torrents/categories":
            return categories_response("movies") if category_created else categories_response()
        if request.url.path == "/api/v2/torrents/createCategory":
            category_created = True
            return httpx.Response(200, text="Ok.")
        if request.url.path == "/api/v2/torrents/add":
            raise httpx.ReadTimeout("add response timed out", request=request)
        raise AssertionError(request.url.path)

    async def category_write_guard() -> None:
        nonlocal category_write_guard_calls
        category_write_guard_calls += 1

    async def add_write_guard() -> None:
        nonlocal add_write_guard_calls
        add_write_guard_calls += 1

    client = adapter(httpx.MockTransport(handler))
    try:
        await client.authenticate()
        with pytest.raises(AppError) as caught:
            await client.add_torrent(
                torrent,
                expected_info_hash=info_hash,
                save_path="/downloads/approved",
                category="movies",
                category_write_guard=category_write_guard,
                write_guard=add_write_guard,
            )
    finally:
        await client.aclose()

    assert caught.value.error_code == "QB_ADD_OUTCOME_UNKNOWN"
    assert caught.value.retryable is False
    assert caught.value.details == {"external_write_may_have_occurred": True}
    assert category_write_guard_calls == 1
    assert add_write_guard_calls == 1
    assert paths == [
        "/api/v2/auth/login",
        "/api/v2/torrents/info",
        "/api/v2/app/webapiVersion",
        "/api/v2/torrents/categories",
        "/api/v2/torrents/createCategory",
        "/api/v2/torrents/categories",
        "/api/v2/torrents/add",
    ]


@pytest.mark.asyncio
async def test_concurrent_category_creation_conflict_is_idempotent() -> None:
    torrent, info_hash = torrent_fixture()
    paths: list[str] = []
    category_reads = 0
    write_guard_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal category_reads
        paths.append(request.url.path)
        if request.url.path == "/api/v2/auth/login":
            return login_response()
        if request.url.path == "/api/v2/torrents/info":
            return httpx.Response(200, json=[])
        if request.url.path == "/api/v2/app/webapiVersion":
            return httpx.Response(200, text="2.11.4")
        if request.url.path == "/api/v2/torrents/categories":
            category_reads += 1
            return categories_response() if category_reads == 1 else categories_response("movies")
        if request.url.path == "/api/v2/torrents/createCategory":
            return httpx.Response(409, text="category already exists")
        if request.url.path == "/api/v2/torrents/add":
            return httpx.Response(200, text="Ok.")
        raise AssertionError(request.url.path)

    async def write_guard() -> None:
        nonlocal write_guard_calls
        write_guard_calls += 1

    client = adapter(httpx.MockTransport(handler))
    try:
        await client.authenticate()
        result = await client.add_torrent(
            torrent,
            expected_info_hash=info_hash,
            save_path="/downloads/approved",
            category="movies",
            write_guard=write_guard,
        )
    finally:
        await client.aclose()

    assert result.outcome == "SUBMITTED"
    assert write_guard_calls == 2
    assert paths == [
        "/api/v2/auth/login",
        "/api/v2/torrents/info",
        "/api/v2/app/webapiVersion",
        "/api/v2/torrents/categories",
        "/api/v2/torrents/createCategory",
        "/api/v2/torrents/categories",
        "/api/v2/torrents/add",
    ]


@pytest.mark.asyncio
async def test_add_state_field_is_cached_only_for_current_sid_session() -> None:
    first_torrent, first_hash = torrent_fixture()
    second_torrent, second_hash = v2_torrent_fixture()
    version_calls = 0
    add_bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal version_calls
        if request.url.path == "/api/v2/auth/login":
            return login_response()
        if request.url.path == "/api/v2/torrents/info":
            return httpx.Response(200, json=[])
        if request.url.path == "/api/v2/app/webapiVersion":
            version_calls += 1
            return httpx.Response(200, text="2.11.4")
        if request.url.path == "/api/v2/torrents/categories":
            return categories_response("movies")
        if request.url.path == "/api/v2/torrents/add":
            add_bodies.append(request.content)
            return httpx.Response(200, text="Ok.")
        raise AssertionError(request.url.path)

    client = adapter(httpx.MockTransport(handler))
    try:
        await client.authenticate()
        for payload, info_hash in (
            (first_torrent, first_hash),
            (second_torrent, second_hash),
        ):
            await client.add_torrent(
                payload,
                expected_info_hash=info_hash,
                save_path="/downloads",
                category="movies",
                start_immediately=False,
            )
        assert version_calls == 1

        await client.authenticate()
        await client.add_torrent(
            first_torrent,
            expected_info_hash=first_hash,
            save_path="/downloads",
            category="movies",
            start_immediately=False,
        )
    finally:
        await client.aclose()

    assert version_calls == 2
    assert len(add_bodies) == 3
    assert all(b'name="stopped"\r\n\r\ntrue\r\n' in body for body in add_bodies)
    assert all(b'name="paused"' not in body for body in add_bodies)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("version_response", "expected_code"),
    [
        (httpx.Response(200, text="2.11"), "QB_WEB_API_VERSION_INVALID"),
        (httpx.Response(200, text="v2.11.0"), "QB_WEB_API_VERSION_INVALID"),
        (httpx.Response(200, text="1.99.0"), "QB_WEB_API_VERSION_UNSUPPORTED"),
        (httpx.Response(200, text="3.0.0"), "QB_WEB_API_VERSION_UNSUPPORTED"),
        (httpx.Response(500, text="failed"), "QB_HTTP_ERROR"),
    ],
)
async def test_version_probe_failure_closes_before_add(
    version_response: httpx.Response,
    expected_code: str,
) -> None:
    torrent, info_hash = torrent_fixture()
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/api/v2/auth/login":
            return login_response()
        if request.url.path == "/api/v2/torrents/info":
            return httpx.Response(200, json=[])
        if request.url.path == "/api/v2/app/webapiVersion":
            return version_response
        raise AssertionError("version failure must prevent the add POST")

    client = adapter(httpx.MockTransport(handler))
    try:
        await client.authenticate()
        with pytest.raises(AppError) as caught:
            await client.add_torrent(
                torrent,
                expected_info_hash=info_hash,
                save_path="/downloads",
                category="movies",
                start_immediately=False,
            )
    finally:
        await client.aclose()

    assert caught.value.error_code == expected_code
    assert paths == [
        "/api/v2/auth/login",
        "/api/v2/torrents/info",
        "/api/v2/app/webapiVersion",
    ]


@pytest.mark.asyncio
async def test_failed_version_probe_is_not_cached() -> None:
    torrent, info_hash = torrent_fixture()
    version_calls = 0
    add_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal add_calls, version_calls
        if request.url.path == "/api/v2/auth/login":
            return login_response()
        if request.url.path == "/api/v2/torrents/info":
            return httpx.Response(200, json=[])
        if request.url.path == "/api/v2/app/webapiVersion":
            version_calls += 1
            return httpx.Response(200, text="2.11" if version_calls == 1 else "2.11.4")
        if request.url.path == "/api/v2/torrents/categories":
            return categories_response("movies")
        if request.url.path == "/api/v2/torrents/add":
            add_calls += 1
            return httpx.Response(200, text="Ok.")
        raise AssertionError(request.url.path)

    client = adapter(httpx.MockTransport(handler))
    try:
        await client.authenticate()
        with pytest.raises(AppError) as caught:
            await client.add_torrent(
                torrent,
                expected_info_hash=info_hash,
                save_path="/downloads",
                category="movies",
                start_immediately=False,
            )
        result = await client.add_torrent(
            torrent,
            expected_info_hash=info_hash,
            save_path="/downloads",
            category="movies",
            start_immediately=False,
        )
    finally:
        await client.aclose()

    assert caught.value.error_code == "QB_WEB_API_VERSION_INVALID"
    assert result.outcome == "SUBMITTED"
    assert version_calls == 2
    assert add_calls == 1


@pytest.mark.asyncio
async def test_ambiguous_add_response_requires_reconciliation() -> None:
    torrent, info_hash = torrent_fixture()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/auth/login":
            return login_response()
        if request.url.path == "/api/v2/torrents/info":
            return httpx.Response(200, json=[])
        if request.url.path == "/api/v2/app/webapiVersion":
            return httpx.Response(200, text="2.11.4")
        if request.url.path == "/api/v2/torrents/categories":
            return categories_response("movies")
        return httpx.Response(500, text="unknown")

    client = adapter(httpx.MockTransport(handler))
    try:
        await client.authenticate()
        with pytest.raises(AppError) as caught:
            await client.add_torrent(
                torrent,
                expected_info_hash=info_hash,
                save_path="/downloads",
                category="movies",
            )
    finally:
        await client.aclose()

    assert caught.value.error_code == "QB_ADD_OUTCOME_UNKNOWN"
    assert caught.value.retryable is False
    assert caught.value.details == {"external_write_may_have_occurred": True}


@pytest.mark.asyncio
async def test_write_guard_runs_after_deduplication_and_before_add_post() -> None:
    torrent, info_hash = torrent_fixture()
    paths: list[str] = []
    guard_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/api/v2/auth/login":
            return login_response()
        if request.url.path == "/api/v2/torrents/info":
            return httpx.Response(200, json=[])
        if request.url.path == "/api/v2/app/webapiVersion":
            return httpx.Response(200, text="2.11.4")
        if request.url.path == "/api/v2/torrents/categories":
            return categories_response("movies")
        if request.url.path == "/api/v2/torrents/add":
            raise AssertionError("expired lease must prevent the add POST")
        raise AssertionError(request.url.path)

    async def reject_expired_lease() -> None:
        nonlocal guard_calls
        guard_calls += 1
        raise AppError(
            "DOWNLOAD_EXECUTION_LEASE_LOST",
            "lease expired",
            status_code=409,
        )

    client = adapter(httpx.MockTransport(handler))
    try:
        await client.authenticate()
        with pytest.raises(AppError) as caught:
            await client.add_torrent(
                torrent,
                expected_info_hash=info_hash,
                save_path="/downloads",
                category="movies",
                write_guard=reject_expired_lease,
            )
    finally:
        await client.aclose()

    assert caught.value.error_code == "DOWNLOAD_EXECUTION_LEASE_LOST"
    assert guard_calls == 1
    assert paths == [
        "/api/v2/auth/login",
        "/api/v2/torrents/info",
        "/api/v2/app/webapiVersion",
        "/api/v2/torrents/categories",
    ]


@pytest.mark.asyncio
async def test_request_guard_blocks_add_after_internal_deduplication_get() -> None:
    torrent, info_hash = torrent_fixture()
    paths: list[str] = []
    guard_calls = 0
    write_guard_calls = 0

    async def request_guard() -> None:
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls == 5:
            raise AppError(
                "AUTOMATION_POLICY_REVISION_CHANGED",
                "policy changed",
                status_code=409,
            )

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/api/v2/auth/login":
            return login_response()
        if request.url.path == "/api/v2/torrents/info":
            return httpx.Response(200, json=[])
        if request.url.path == "/api/v2/app/webapiVersion":
            return httpx.Response(200, text="2.11.4")
        if request.url.path == "/api/v2/torrents/categories":
            return categories_response("movies")
        raise AssertionError("request guard must block the add POST")

    async def write_guard() -> None:
        nonlocal write_guard_calls
        write_guard_calls += 1

    client = adapter(
        httpx.MockTransport(handler),
        before_request=request_guard,
    )
    try:
        await client.authenticate()
        with pytest.raises(AppError) as caught:
            await client.add_torrent(
                torrent,
                expected_info_hash=info_hash,
                save_path="/downloads",
                category="movies",
                write_guard=write_guard,
            )
    finally:
        await client.aclose()

    assert caught.value.error_code == "AUTOMATION_POLICY_REVISION_CHANGED"
    assert caught.value.details["external_request_performed"] is False
    assert guard_calls == 5
    assert write_guard_calls == 0
    assert paths == [
        "/api/v2/auth/login",
        "/api/v2/torrents/info",
        "/api/v2/app/webapiVersion",
        "/api/v2/torrents/categories",
    ]
