from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest

from app.adapters.pt_sites.nexusphp import SameOriginNexusSession
from app.core.http import SafeAsyncHttpClient
from app.errors import AppError

BASE_URL = "https://upstream.example.test"


async def reject_proxy_connect(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    try:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(
            b"HTTP/1.1 407 Proxy Authentication Required\r\n"
            b"Content-Length: 0\r\n\r\n"
        )
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


class DisconnectingStream(httpx.AsyncByteStream):
    def __init__(self, error: httpx.HTTPError) -> None:
        self.error = error
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"partial response"
        raise self.error

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_code", "expected_status"),
    (
        (httpx.ReadTimeout("response body timed out"), "UPSTREAM_TIMEOUT", 504),
        (
            httpx.RemoteProtocolError("response body disconnected"),
            "UPSTREAM_NETWORK_ERROR",
            502,
        ),
    ),
)
async def test_response_body_http_errors_are_retryable_and_close_stream(
    error: httpx.HTTPError,
    expected_code: str,
    expected_status: int,
) -> None:
    stream = DisconnectingStream(error)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=stream,
        )

    client = SafeAsyncHttpClient(
        base_url=BASE_URL,
        allowed_hosts=("upstream.example.test",),
        connect_timeout=1,
        read_timeout=1,
        max_response_bytes=1024,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(AppError) as caught:
            await client.request("GET", "/resource")

        assert caught.value.error_code == expected_code
        assert caught.value.status_code == expected_status
        assert caught.value.retryable is True
        assert caught.value.__cause__ is error
        assert stream.closed is True
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_nexusphp_proxy_defers_target_dns_resolution_to_proxy() -> None:
    async def forbidden_resolver(_host: str, _port: int) -> tuple[str, ...]:
        raise AssertionError("proxied target must be resolved by the proxy")

    server = await asyncio.start_server(reject_proxy_connect, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    session = SameOriginNexusSession(
        BASE_URL,
        allowed_hosts=("upstream.example.test",),
        proxy=httpx.Proxy(f"http://127.0.0.1:{port}"),
        address_resolver=forbidden_resolver,
        connect_timeout=1,
        read_timeout=1,
        max_response_bytes=1024,
    )
    try:
        with pytest.raises(AppError) as caught:
            await session.request("GET", "/resource")
        assert caught.value.error_code == "NEXUSPHP_NETWORK_ERROR"
    finally:
        await session.aclose()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_connect_407_is_reported_as_proxy_authentication_failure() -> None:
    server = await asyncio.start_server(reject_proxy_connect, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    client = SafeAsyncHttpClient(
        base_url=BASE_URL,
        allowed_hosts=("upstream.example.test",),
        connect_timeout=1,
        read_timeout=1,
        max_response_bytes=1024,
        proxy=httpx.Proxy(f"http://127.0.0.1:{port}"),
    )
    try:
        with pytest.raises(AppError) as caught:
            await client.request("GET", "/resource")

        assert caught.value.error_code == "OUTBOUND_PROXY_AUTH_FAILED"
        assert caught.value.message == "出站代理认证失败"
        assert caught.value.retryable is False
    finally:
        await client.aclose()
        server.close()
        await server.wait_closed()
