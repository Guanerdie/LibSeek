"""The delete path of the qBittorrent adapter, which erases files."""

from __future__ import annotations

import httpx
import pytest

from app.adapters.downloaders.qbittorrent import QbittorrentAdapter
from app.errors import AppError

BASE = "https://qb.example.test"
V1 = "a" * 40
V2 = "b" * 64


def _adapter(
    transport: httpx.AsyncBaseTransport,
    *,
    enable_write: bool = True,
    enable_delete: bool = True,
) -> QbittorrentAdapter:
    return QbittorrentAdapter(
        base_url=BASE,
        username="runtime-user",
        password="runtime-password",
        allowed_hosts=("qb.example.test",),
        transport=transport,
        enable_write=enable_write,
        enable_delete=enable_delete,
    )


class Recorder:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, bytes]] = []

    def transport(self) -> httpx.AsyncBaseTransport:
        async def handle(request: httpx.Request) -> httpx.Response:
            body = request.content
            self.requests.append((request.method, request.url.path, body))
            if request.url.path.endswith("/auth/login"):
                return httpx.Response(
                    200, text="Ok.", headers={"set-cookie": "SID=token; path=/"}
                )
            return httpx.Response(200, text="")

        return httpx.MockTransport(handle)

    @property
    def last_body(self) -> str:
        return self.requests[-1][2].decode()


async def _authenticated(adapter: QbittorrentAdapter) -> QbittorrentAdapter:
    await adapter.authenticate()
    return adapter


@pytest.mark.asyncio
async def test_delete_requires_its_own_authorisation() -> None:
    recorder = Recorder()
    adapter = await _authenticated(
        _adapter(recorder.transport(), enable_write=True, enable_delete=False)
    )
    try:
        with pytest.raises(AppError) as caught:
            await adapter.delete_torrents([V1], delete_files=True)
        assert caught.value.error_code == "QB_DELETE_DISABLED"
        assert not any(path.endswith("/torrents/delete") for _, path, _ in recorder.requests)
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_write_authorisation_alone_does_not_grant_delete() -> None:
    recorder = Recorder()
    adapter = await _authenticated(
        _adapter(recorder.transport(), enable_write=False, enable_delete=True)
    )
    try:
        with pytest.raises(AppError) as caught:
            await adapter.delete_torrents([V1], delete_files=True)
        assert caught.value.error_code == "QB_DELETE_DISABLED"
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_delete_sends_the_files_flag() -> None:
    recorder = Recorder()
    adapter = await _authenticated(_adapter(recorder.transport()))
    try:
        await adapter.delete_torrents([V1], delete_files=True)
        assert f"hashes={V1}" in recorder.last_body
        assert "deleteFiles=true" in recorder.last_body
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_v2_hash_is_also_sent_truncated() -> None:
    """qBittorrent addresses a v2 torrent by its 40-character id."""

    recorder = Recorder()
    adapter = await _authenticated(_adapter(recorder.transport()))
    try:
        await adapter.add_tags([V2], ["unin-cleanup"])
        body = recorder.last_body
        assert V2 in body
        assert V2[:40] in body.replace(V2, "")
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_malformed_hash_is_refused() -> None:
    recorder = Recorder()
    adapter = await _authenticated(_adapter(recorder.transport()))
    try:
        with pytest.raises(AppError) as caught:
            await adapter.delete_torrents(["not-a-hash"], delete_files=True)
        assert caught.value.error_code == "QB_INFO_HASH_INVALID"
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_too_many_hashes_is_refused() -> None:
    recorder = Recorder()
    adapter = await _authenticated(_adapter(recorder.transport()))
    try:
        many = [f"{index:040x}" for index in range(60)]
        with pytest.raises(AppError) as caught:
            await adapter.delete_torrents(many, delete_files=True)
        assert caught.value.error_code == "QB_TOO_MANY_HASHES"
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_empty_tag_list_sends_nothing() -> None:
    recorder = Recorder()
    adapter = await _authenticated(_adapter(recorder.transport()))
    try:
        await adapter.add_tags([V1], [" "])
        assert not any(path.endswith("/torrents/addTags") for _, path, _ in recorder.requests)
    finally:
        await adapter.aclose()
