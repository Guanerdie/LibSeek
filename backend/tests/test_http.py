from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from app.core.http import SafeAsyncHttpClient
from app.errors import AppError

BASE_URL = "https://upstream.example.test"


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
