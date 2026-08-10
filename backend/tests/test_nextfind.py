from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from app.adapters.media_sources.nextfind import NextFindAdapter
from app.errors import AppError
from app.models.enums import IdentityConfidence, MediaType

BASE_URL = "https://nextfind.example"


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk


@pytest.fixture
def adapter() -> NextFindAdapter:
    return NextFindAdapter(
        base_url=BASE_URL,
        allowed_hosts=("nextfind.example",),
        username="reader",
        password="super-secret",
    )


@pytest.mark.asyncio
@respx.mock
async def test_login_success_sets_authenticated(adapter: NextFindAdapter) -> None:
    route = respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(
            200,
            json={"success": True},
            headers={"set-cookie": "session=safe-test; Path=/; HttpOnly"},
        )
    )
    await adapter.authenticate()
    assert route.called
    assert adapter.cookies.get("session") == "safe-test"
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_login_failure_is_stable_and_logs_no_secrets(
    adapter: NextFindAdapter, caplog: pytest.LogCaptureFixture
) -> None:
    respx.post(f"{BASE_URL}/api/admin/login").mock(return_value=httpx.Response(401))
    with caplog.at_level(logging.DEBUG), pytest.raises(AppError) as caught:
        await adapter.authenticate()
    assert caught.value.error_code == "AUTH_FAILED"
    assert "super-secret" not in caplog.text
    assert "cookie" not in caplog.text.casefold()
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_session_expiry_is_not_an_empty_list(adapter: NextFindAdapter) -> None:
    respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    respx.get(f"{BASE_URL}/api/discover").mock(return_value=httpx.Response(401))
    with pytest.raises(AppError) as caught:
        await adapter.list_missing_media()
    assert caught.value.error_code == "AUTH_EXPIRED"
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_ndjson_chunks_blank_and_bad_lines_are_isolated(adapter: NextFindAdapter) -> None:
    respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    chunks = [
        b'{"data":{"id":123,"tmdb_id":999,"type":"tv","title":"Example",',
        b'"year":"2026","local_episodes":2,"total_episodes":8,',
        b'"aired_episodes":6,"missing_episodes":["S01E03"]}}\n\n{broken',
        b' json}\n{"data":{"type":"movie","title":"No ID","year":2025}}',
    ]
    respx.get(f"{BASE_URL}/api/discover").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/x-ndjson"},
            stream=ChunkStream(chunks),
        )
    )
    result = await adapter.list_missing_media()
    assert len(result.items) == 2
    assert len(result.warnings) == 1
    assert result.warnings[0].error_code == "NDJSON_BAD_LINE"
    first, second = result.items
    assert first.tmdb_id == 123
    assert first.media_type == MediaType.TV
    assert first.missing_episodes == ["S01E03"]
    assert first.identity_confidence == IdentityConfidence.HIGH
    assert second.tmdb_id is None
    assert second.source_item_id.startswith("nextfind:temporary:")
    assert second.identity_confidence == IdentityConfidence.NEEDS_CONFIRMATION
    await adapter.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error_code"),
    [(403, "AUTH_FORBIDDEN"), (429, "UPSTREAM_RATE_LIMITED"), (500, "UPSTREAM_UNAVAILABLE")],
)
@respx.mock
async def test_upstream_status_codes(
    adapter: NextFindAdapter, status: int, error_code: str
) -> None:
    respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    respx.get(f"{BASE_URL}/api/discover").mock(return_value=httpx.Response(status))
    with pytest.raises(AppError) as caught:
        await adapter.list_missing_media()
    assert caught.value.error_code == error_code
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_timeout_and_non_json_have_stable_codes(adapter: NextFindAdapter) -> None:
    respx.post(f"{BASE_URL}/api/admin/login").mock(side_effect=httpx.ReadTimeout("timed out"))
    with pytest.raises(AppError) as timeout:
        await adapter.authenticate()
    assert timeout.value.error_code == "UPSTREAM_TIMEOUT"

    respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(200, text="login page", headers={"content-type": "text/html"})
    )
    with pytest.raises(AppError) as non_json:
        await adapter.authenticate()
    assert non_json.value.error_code == "UPSTREAM_NON_JSON"
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_library_details_are_validated(adapter: NextFindAdapter) -> None:
    respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    respx.get(f"{BASE_URL}/api/local_library").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "local_episodes": 4,
                    "total_episodes": 10,
                    "aired_episodes": 8,
                    "missing_episodes": ["S01E05", "S01E06"],
                }
            },
        )
    )
    details = await adapter.get_library_details(MediaType.TV, 123)
    assert details.tmdb_id == 123
    assert details.missing_episodes == ["S01E05", "S01E06"]
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_cursor_pagination_reads_all_pages(adapter: NextFindAdapter) -> None:
    respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(200, json={"success": True})
    )

    def discover(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("cursor") == "next-page":
            body = b'{"data":{"id":2,"type":"movie","title":"Second"}}\n'
        else:
            body = (
                b'{"data":{"id":1,"type":"movie","title":"First"},'
                b'"next_cursor":"next-page"}\n'
            )
        return httpx.Response(
            200, content=body, headers={"content-type": "application/x-ndjson"}
        )

    route = respx.get(f"{BASE_URL}/api/discover").mock(side_effect=discover)
    result = await adapter.list_missing_media()
    assert route.call_count == 2
    assert [item.tmdb_id for item in result.items] == [1, 2]
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_redirect_target_is_revalidated(adapter: NextFindAdapter) -> None:
    respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(307, headers={"location": "https://evil.invalid/login"})
    )
    with pytest.raises(AppError) as caught:
        await adapter.authenticate()
    assert caught.value.error_code == "EXTERNAL_HOST_NOT_ALLOWED"
    await adapter.aclose()


@pytest.mark.asyncio
async def test_default_respx_policy_blocks_unmatched_real_network(adapter: NextFindAdapter) -> None:
    with respx.mock(assert_all_mocked=True), pytest.raises(AssertionError):
        await adapter.authenticate()
    await adapter.aclose()
