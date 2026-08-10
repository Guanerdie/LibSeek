from __future__ import annotations

import hashlib

import bencodepy
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
    transport: httpx.AsyncBaseTransport, *, enable_write: bool = True
) -> QbittorrentAdapter:
    return QbittorrentAdapter(
        base_url=BASE,
        username="runtime-user",
        password="runtime-password",
        allowed_hosts=("qb.example.test",),
        transport=transport,
        enable_write=enable_write,
    )


def login_response() -> httpx.Response:
    return httpx.Response(
        200,
        text="Ok.",
        headers={"Set-Cookie": "SID=memory-session; HttpOnly; Path=/"},
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
async def test_low_level_add_request_cannot_bypass_write_gate() -> None:
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
                "/api/v2/torrents/add",
                files={"torrents": ("approved.torrent", b"payload", "application/x-bittorrent")},
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

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/api/v2/auth/login":
            return login_response()
        if request.url.path == "/api/v2/torrents/info":
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
async def test_new_torrent_is_submitted_with_approved_target() -> None:
    torrent, info_hash = torrent_fixture()
    add_body = b""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal add_body
        if request.url.path == "/api/v2/auth/login":
            return login_response()
        if request.url.path == "/api/v2/torrents/info":
            return httpx.Response(200, json=[])
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
            start_immediately=False,
        )
    finally:
        await client.aclose()

    assert result.outcome == "SUBMITTED"
    assert torrent in add_body
    for value in (b"/downloads/approved", b"movies", b"unin,approved", b"true"):
        assert value in add_body


@pytest.mark.asyncio
async def test_ambiguous_add_response_requires_reconciliation() -> None:
    torrent, info_hash = torrent_fixture()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/auth/login":
            return login_response()
        if request.url.path == "/api/v2/torrents/info":
            return httpx.Response(200, json=[])
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
