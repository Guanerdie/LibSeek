from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from app.adapters.media_sources.nextfind import NextFindAdapter, NextFindRawItem
from app.errors import AppError
from app.models.enums import IdentityConfidence, MediaType
from app.schemas.adapters import LibraryDetails

BASE_URL = "https://nextfind.example"


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk


class DisconnectingStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], error: httpx.HTTPError) -> None:
        self.chunks = chunks
        self.error = error

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk
        raise self.error


async def no_retry_sleep(_: float) -> None:
    return None


@pytest.fixture
def adapter() -> NextFindAdapter:
    return NextFindAdapter(
        base_url=BASE_URL,
        allowed_hosts=("nextfind.example",),
        username="reader",
        password="super-secret",
        discover_page_size=100,
        stream_retry_attempts=3,
        retry_sleep=no_retry_sleep,
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
async def test_probe_authenticates_then_reads_one_missing_item_without_disclosing_it(
    adapter: NextFindAdapter,
) -> None:
    private_title = "PRIVATE MEDIA TITLE"
    login = respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    discover = respx.get(f"{BASE_URL}/api/discover").mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"title": private_title, "type": "movie"}]},
        )
    )

    result = await adapter.probe()

    assert result.healthy is True
    assert result.message == "NextFind 登录与未入库列表验证成功"
    assert private_title not in result.model_dump_json()
    assert login.call_count == 1
    assert discover.call_count == 1
    request = discover.calls[0].request
    assert dict(request.url.params) == {
        "status": "未入库",
        "page": "1",
        "page_size": "1",
        "sort": "updated_at",
    }
    await adapter.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error_code"),
    ((401, "AUTH_EXPIRED"), (500, "UPSTREAM_UNAVAILABLE")),
)
@respx.mock
async def test_probe_preserves_discover_status_mapping(
    adapter: NextFindAdapter, status: int, error_code: str
) -> None:
    respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    respx.get(f"{BASE_URL}/api/discover").mock(return_value=httpx.Response(status))

    result = await adapter.probe()

    assert result.healthy is False
    assert result.error_code == error_code
    await adapter.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "error_code"),
    (
        (
            httpx.Response(
                200,
                text="<html>not a discover response</html>",
                headers={"content-type": "text/html"},
            ),
            "UPSTREAM_NON_JSON",
        ),
        (
            httpx.Response(
                200,
                content=b'{"data":',
                headers={"content-type": "application/json"},
            ),
            "UPSTREAM_INVALID_JSON",
        ),
    ),
)
@respx.mock
async def test_probe_rejects_invalid_discover_responses(
    adapter: NextFindAdapter, response: httpx.Response, error_code: str
) -> None:
    respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    respx.get(f"{BASE_URL}/api/discover").mock(return_value=response)

    result = await adapter.probe()

    assert result.healthy is False
    assert result.error_code == error_code
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_probe_does_not_read_discover_when_login_fails(
    adapter: NextFindAdapter,
) -> None:
    login = respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(401)
    )
    discover = respx.get(f"{BASE_URL}/api/discover").mock(
        return_value=httpx.Response(200, json=[])
    )

    result = await adapter.probe()

    assert result.healthy is False
    assert result.error_code == "AUTH_FAILED"
    assert login.call_count == 1
    assert discover.call_count == 0
    await adapter.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content_type", "content"),
    (
        ("application/json", b"[]"),
        ("application/json", b'{"items":[]}'),
        ("application/json", b'{"list":[]}'),
        ("application/x-ndjson", b'{"data":[]}\n'),
    ),
)
@respx.mock
async def test_probe_accepts_supported_empty_and_stream_shapes(
    adapter: NextFindAdapter, content_type: str, content: bytes
) -> None:
    respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    respx.get(f"{BASE_URL}/api/discover").mock(
        return_value=httpx.Response(
            200,
            content=content,
            headers={"content-type": content_type},
        )
    )

    result = await adapter.probe()

    assert result.healthy is True
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_probe_accepts_real_ndjson_data_and_control_segment_shape(
    adapter: NextFindAdapter,
) -> None:
    respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    respx.get(f"{BASE_URL}/api/discover").mock(
        return_value=httpx.Response(
            200,
            content=(
                b'{"data":[{"title":"One","type":"movie"}],'
                b'"type":"result","current_page":1,"total_pages":1,'
                b'"page_size":1,"total_results":1,"next_cursor":null}\n'
                b'{"data":[]}\n'
                b'{"type":"pagination","status":"complete","total_pages":1,'
                b'"page_size":1,"next_cursor":null}\n'
            ),
            headers={"content-type": "application/x-ndjson"},
        )
    )

    result = await adapter.probe()

    assert result.healthy is True
    await adapter.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    (
        b"",
        b'{"type":"pagination","status":"complete","total_pages":1,'
        b'"page_size":1,"next_cursor":null}\n',
        b'{"data":[]}\n{"unexpected":"object"}\n',
        b'{"data":[],"unexpected":"object"}\n',
        b'{"data":[],"total_pages":"one"}\n',
        b'{"data":[]}\n{"type":"pagination","page_size":"one"}\n',
        b'{"data":[]}\n{"type":"pagination","current_page":0}\n',
        b'{"data":[]}\n{"type":"pagination","total_results":-1}\n',
        b'{"data":{"title":"One"}}\n',
        b'{"data":[]}\n{"type":\n',
    ),
)
@respx.mock
async def test_probe_rejects_malformed_ndjson(
    adapter: NextFindAdapter, content: bytes
) -> None:
    respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    respx.get(f"{BASE_URL}/api/discover").mock(
        return_value=httpx.Response(
            200,
            content=content,
            headers={"content-type": "application/x-ndjson"},
        )
    )

    result = await adapter.probe()

    assert result.healthy is False
    assert result.error_code == "UPSTREAM_INVALID_JSON"
    await adapter.aclose()


@pytest.mark.asyncio
async def test_nextfind_client_ignores_ambient_proxy_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://private-proxy.invalid:8080")
    monkeypatch.delenv("NO_PROXY", raising=False)
    proxy_safe_adapter = NextFindAdapter(
        base_url=BASE_URL,
        allowed_hosts=("nextfind.example",),
        username="reader",
        password="super-secret",
    )
    try:
        assert proxy_safe_adapter._client._trust_env is False
        assert proxy_safe_adapter._client._mounts == {}
    finally:
        await proxy_safe_adapter.aclose()


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
async def test_transient_login_404_retries_and_recovers(adapter: NextFindAdapter) -> None:
    route = respx.post(f"{BASE_URL}/api/admin/login").mock(
        side_effect=[
            httpx.Response(404),
            httpx.Response(
                200,
                json={"success": True},
                headers={"set-cookie": "session=recovered; Path=/; HttpOnly"},
            ),
        ]
    )

    await adapter.authenticate()

    assert route.call_count == 2
    assert adapter._authenticated is True
    assert adapter.cookies.get("session") == "recovered"
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_persistent_login_404_is_retryable_after_bounded_retries(
    adapter: NextFindAdapter,
) -> None:
    route = respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(404)
    )

    with pytest.raises(AppError) as caught:
        await adapter.authenticate()

    assert route.call_count == 3
    assert caught.value.error_code == "UPSTREAM_AUTH_UNAVAILABLE"
    assert caught.value.retryable is True
    assert adapter._authenticated is False
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
        b'"year":"2026","local_episodes":2,"existing_episodes":',
        b'["S01E01","S01E03",{"season_number":2,"episode_number":1}],',
        b'"total_episodes":8,',
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
    await adapter.authenticate()
    items, warnings = await adapter._list_missing_media_type("电影")
    assert len(items) == 2
    assert len(warnings) == 1
    assert warnings[0].error_code == "NDJSON_BAD_LINE"
    first, second = items
    assert first.tmdb_id == 123
    assert first.media_type == MediaType.TV
    assert first.local_episode_matrix == {1: [1, 3], 2: [1]}
    assert first.missing_episodes == ["S01E03"]
    assert first.identity_confidence == IdentityConfidence.HIGH
    assert second.tmdb_id is None
    assert second.source_item_id.startswith("nextfind:temporary:")
    assert second.identity_confidence == IdentityConfidence.NEEDS_CONFIRMATION
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_nextfind_normalizes_country_code_aliases(adapter: NextFindAdapter) -> None:
    respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    respx.get(f"{BASE_URL}/api/discover").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": 1,
                        "type": "movie",
                        "title": "First",
                        "original_language": " JA ",
                        "country": " jp ",
                        "countries": ["US", "jp", "Japan"],
                        "production_countries": [
                            {"iso_3166_1": "kr"},
                            {"iso_3166_1": "US"},
                            {"name": "Unknown"},
                        ],
                    },
                    {
                        "id": 2,
                        "type": "tv",
                        "title": "Second",
                        "origin_country": '["th", "TH", "invalid"]',
                    },
                    {
                        "id": 3,
                        "type": "movie",
                        "title": "Third",
                        "country_codes": ["France", 7, None],
                    },
                ]
            },
        )
    )

    result = await adapter.list_missing_media()

    assert [item.country_codes for item in result.items] == [
        ["JP", "US", "KR"],
        ["TH"],
        None,
    ]
    assert [item.original_language for item in result.items] == ["ja", None, None]
    assert not result.warnings
    await adapter.aclose()


@pytest.mark.parametrize(
    "origin_country",
    (
        '"JP"',
        '{"code":"JP"}',
        '["JP"',
        '["JP",7]',
        "[]",
        "[" + ",".join('"JP"' for _ in range(65)) + "]",
        "[" + '"JP"' * 600 + "]",
    ),
)
def test_nextfind_rejects_invalid_origin_country_json(origin_country: str) -> None:
    raw = NextFindRawItem.model_validate(
        {"id": 1, "type": "movie", "title": "Invalid", "origin_country": origin_country}
    )

    assert raw.country_codes is None


def test_nextfind_accepts_origin_country_array_and_two_letter_fallback() -> None:
    array_value = NextFindRawItem.model_validate(
        {"id": 1, "type": "movie", "title": "Array", "origin_country": ["jp", "US"]}
    )
    direct_value = NextFindRawItem.model_validate(
        {"id": 2, "type": "movie", "title": "Direct", "origin_country": "kr"}
    )

    assert array_value.country_codes == ["JP", "US"]
    assert direct_value.country_codes == ["KR"]


@pytest.mark.asyncio
@respx.mock
async def test_nextfind_keeps_country_codes_when_a_duplicate_summary_follows(
    adapter: NextFindAdapter,
) -> None:
    respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    respx.get(f"{BASE_URL}/api/discover").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": 1,
                        "type": "movie",
                        "title": "Detailed",
                        "origin_country": '["JP","US"]',
                        "original_language": "ja",
                    },
                    {"id": 1, "type": "movie", "title": "Summary"},
                ]
            },
        )
    )

    result = await adapter.list_missing_media()

    assert len(result.items) == 1
    assert result.items[0].country_codes == ["JP", "US"]
    assert result.items[0].original_language == "ja"
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
                    "local_episode_matrix": {"1": [1, 2, 4], "2": [1]},
                    "total_episodes": 10,
                    "aired_episodes": 8,
                    "missing_episodes": ["S01E05", "S01E06"],
                }
            },
        )
    )
    details = await adapter.get_library_details(MediaType.TV, 123)
    assert details.tmdb_id == 123
    assert details.local_episode_matrix == {1: [1, 2, 4], 2: [1]}
    assert details.missing_episodes == ["S01E05", "S01E06"]
    await adapter.aclose()


def test_library_details_schema_normalizes_matrix_and_exact_missing_codes() -> None:
    details = LibraryDetails.model_validate(
        {
            "tmdb_id": 123,
            "media_type": "tv",
            "local_episode_matrix": {"S01": ["E02", 1, 2]},
            "missing_episodes": ["s01e04", "S01E04"],
        }
    )

    assert details.local_episode_matrix == {1: [1, 2]}
    assert details.missing_episodes == ["S01E04"]


@pytest.mark.asyncio
@respx.mock
async def test_discovery_requests_both_nextfind_types_and_merges_them(
    adapter: NextFindAdapter,
) -> None:
    login = respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(200, json={"success": True})
    )

    def discover(request: httpx.Request) -> httpx.Response:
        requested_type = request.url.params["type"]
        raw_type = "movie" if requested_type == "电影" else "tv"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": 42,
                        "type": raw_type,
                        "title": f"{requested_type} result",
                    }
                ],
                "current_page": 1,
                "total_pages": 1,
                "next_cursor": None,
            },
        )

    route = respx.get(f"{BASE_URL}/api/discover").mock(side_effect=discover)

    result = await adapter.list_missing_media()

    assert login.call_count == 2
    assert [dict(call.request.url.params) for call in route.calls] == [
        {
            "status": "未入库",
            "type": "电影",
            "page": "1",
            "page_size": "100",
            "sort": "updated_at",
        },
        {
            "status": "未入库",
            "type": "电视剧",
            "page": "1",
            "page_size": "100",
            "sort": "updated_at",
        },
    ]
    assert [(item.tmdb_id, item.media_type) for item in result.items] == [
        (42, MediaType.MOVIE),
        (42, MediaType.TV),
    ]
    assert not result.warnings
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_discovery_isolates_pagination_and_page_fingerprints_by_type(
    adapter: NextFindAdapter,
) -> None:
    respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(200, json={"success": True})
    )

    def discover(request: httpx.Request) -> httpx.Response:
        requested_type = request.url.params["type"]
        raw_type = "movie" if requested_type == "电影" else "tv"
        page = int(request.url.params["page"])
        if page == 1:
            item_id = 1
            next_cursor = "shared-next-page"
        else:
            item_id = 2 if raw_type == "movie" else 3
            next_cursor = None
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": item_id,
                        "type": raw_type,
                        "title": f"{requested_type} page {page}",
                    }
                ],
                "current_page": page,
                "total_pages": 2,
                "next_cursor": next_cursor,
            },
        )

    route = respx.get(f"{BASE_URL}/api/discover").mock(side_effect=discover)

    result = await adapter.list_missing_media()

    assert [dict(call.request.url.params) for call in route.calls] == [
        {
            "status": "未入库",
            "type": "电影",
            "page": "1",
            "page_size": "100",
            "sort": "updated_at",
        },
        {
            "status": "未入库",
            "type": "电影",
            "page": "2",
            "page_size": "100",
            "sort": "updated_at",
            "cursor": "shared-next-page",
        },
        {
            "status": "未入库",
            "type": "电视剧",
            "page": "1",
            "page_size": "100",
            "sort": "updated_at",
        },
        {
            "status": "未入库",
            "type": "电视剧",
            "page": "2",
            "page_size": "100",
            "sort": "updated_at",
            "cursor": "shared-next-page",
        },
    ]
    assert {
        (item.tmdb_id, item.media_type)
        for item in result.items
    } == {
        (1, MediaType.MOVIE),
        (2, MediaType.MOVIE),
        (1, MediaType.TV),
        (3, MediaType.TV),
    }
    warning_codes = {warning.error_code for warning in result.warnings}
    assert "PAGINATION_LOOP_ISOLATED" not in warning_codes
    assert "CURSOR_LOOP_ISOLATED" not in warning_codes
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
    await adapter.authenticate()
    items, warnings = await adapter._list_missing_media_type("电影")
    assert route.call_count == 2
    assert [item.tmdb_id for item in items] == [1, 2]
    assert not warnings
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_total_pages_stops_after_a_full_final_page(
    adapter: NextFindAdapter,
) -> None:
    respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(200, json={"success": True})
    )

    def discover(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        assert page in {1, 2}, "total_pages must prevent a request for page 3"
        start = 1 if page == 1 else 101
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": item_id, "type": "movie", "title": f"Movie {item_id}"}
                    for item_id in range(start, start + 100)
                ],
                "type": "result",
                "status": "complete",
                "current_page": page,
                "total_pages": 2,
                "next_cursor": None,
            },
        )

    route = respx.get(f"{BASE_URL}/api/discover").mock(side_effect=discover)

    await adapter.authenticate()
    items, warnings = await adapter._list_missing_media_type("电影")

    assert route.call_count == 2
    assert len(items) == 200
    assert {item.tmdb_id for item in items} == set(range(1, 201))
    assert not warnings
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_retry_404_after_later_page_disconnect_reauthenticates_and_recovers(
    adapter: NextFindAdapter,
) -> None:
    login_route = respx.post(f"{BASE_URL}/api/admin/login").mock(
        side_effect=[
            httpx.Response(
                200,
                json={"success": True},
                headers={"set-cookie": "session=old-session; Path=/; HttpOnly"},
            ),
            httpx.Response(404),
            httpx.Response(
                200,
                json={"success": True},
                headers={"set-cookie": "session=new-session; Path=/; HttpOnly"},
            ),
        ]
    )
    attempt = 0

    def discover(request: httpx.Request) -> httpx.Response:
        nonlocal attempt
        attempt += 1
        expected_session = "new-session" if attempt == 4 else "old-session"
        assert f"session={expected_session}" in request.headers.get("cookie", "")
        if attempt == 1:
            assert request.url.params["page"] == "1"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": item_id, "type": "movie", "title": f"Movie {item_id}"}
                        for item_id in range(1, 101)
                    ],
                    "current_page": 1,
                    "total_pages": 2,
                    "next_cursor": None,
                },
            )
        assert request.url.params["page"] == "2"
        if attempt == 2:
            return httpx.Response(
                200,
                headers={"content-type": "application/x-ndjson"},
                stream=DisconnectingStream(
                    [
                        b'{"data":{"id":999,"type":"movie",'
                        b'"title":"Partial sentinel"},"current_page":2,'
                        b'"total_pages":2}\n'
                    ],
                    httpx.RemoteProtocolError("peer disconnected during response body"),
                ),
            )
        if attempt == 3:
            return httpx.Response(404)
        return httpx.Response(
            200,
            json={
                "data": [{"id": 101, "type": "movie", "title": "Recovered"}],
                "current_page": 2,
                "total_pages": 2,
                "next_cursor": None,
            },
        )

    route = respx.get(f"{BASE_URL}/api/discover").mock(side_effect=discover)

    await adapter.authenticate()
    items, warnings = await adapter._list_missing_media_type("电影")

    assert route.call_count == 4
    assert login_route.call_count == 3
    requests = [dict(call.request.url.params) for call in route.calls]
    assert requests[1] == requests[2] == requests[3]
    assert requests[1]["page"] == "2"
    assert {item.tmdb_id for item in items} == {*range(1, 101), 101}
    assert all(item.title != "Partial sentinel" for item in items)
    assert not warnings
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_retry_404_after_later_page_disconnect_becomes_retryable_after_reauth(
    adapter: NextFindAdapter,
) -> None:
    login_route = respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    attempt = 0

    def discover(request: httpx.Request) -> httpx.Response:
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            assert request.url.params["page"] == "1"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": item_id, "type": "movie", "title": f"Movie {item_id}"}
                        for item_id in range(1, 101)
                    ],
                    "current_page": 1,
                    "total_pages": 2,
                    "next_cursor": None,
                },
            )
        assert request.url.params["page"] == "2"
        if attempt == 2:
            return httpx.Response(
                200,
                headers={"content-type": "application/x-ndjson"},
                stream=DisconnectingStream(
                    [
                        b'{"data":{"id":999,"type":"movie",'
                        b'"title":"Partial sentinel"},"current_page":2,'
                        b'"total_pages":2}\n'
                    ],
                    httpx.RemoteProtocolError("peer disconnected during response body"),
                ),
            )
        return httpx.Response(404)

    route = respx.get(f"{BASE_URL}/api/discover").mock(side_effect=discover)

    await adapter.authenticate()
    with pytest.raises(AppError) as caught:
        await adapter._list_missing_media_type("电影")

    assert route.call_count == 4
    assert login_route.call_count == 2
    requests = [dict(call.request.url.params) for call in route.calls]
    assert requests[1] == requests[2] == requests[3]
    assert requests[1]["page"] == "2"
    assert caught.value.error_code == "UPSTREAM_PAGINATION_UNSTABLE"
    assert caught.value.retryable is True
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_retry_404_after_disconnect_propagates_reauthentication_failure(
    adapter: NextFindAdapter,
) -> None:
    login_route = respx.post(f"{BASE_URL}/api/admin/login").mock(
        side_effect=[
            httpx.Response(200, json={"success": True}),
            httpx.Response(401),
        ]
    )
    attempt = 0

    def discover(_: httpx.Request) -> httpx.Response:
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            return httpx.Response(
                200,
                headers={"content-type": "application/x-ndjson"},
                stream=DisconnectingStream(
                    [b'{"data":{"id":999,"type":"movie","title":"Partial"}}\n'],
                    httpx.RemoteProtocolError("peer disconnected during response body"),
                ),
            )
        return httpx.Response(404)

    route = respx.get(f"{BASE_URL}/api/discover").mock(side_effect=discover)

    await adapter.authenticate()
    with pytest.raises(AppError) as caught:
        await adapter._list_missing_media_type("电影")

    assert route.call_count == 2
    assert login_route.call_count == 2
    assert caught.value.error_code == "AUTH_FAILED"
    assert adapter._authenticated is False
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_first_page_404_is_still_an_upstream_error(
    adapter: NextFindAdapter,
) -> None:
    respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    route = respx.get(f"{BASE_URL}/api/discover").mock(
        return_value=httpx.Response(404)
    )

    with pytest.raises(AppError) as caught:
        await adapter.list_missing_media()

    assert route.call_count == 1
    assert caught.value.error_code == "UPSTREAM_HTTP_ERROR"
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_first_page_disconnect_then_404_becomes_retryable_after_reauthentication(
    adapter: NextFindAdapter,
) -> None:
    login_route = respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    attempt = 0

    def discover(_: httpx.Request) -> httpx.Response:
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            return httpx.Response(
                200,
                headers={"content-type": "application/x-ndjson"},
                stream=DisconnectingStream(
                    [b'{"data":{"id":999,"type":"movie","title":"Partial"}}\n'],
                    httpx.RemoteProtocolError("peer disconnected during response body"),
                ),
            )
        return httpx.Response(404)

    route = respx.get(f"{BASE_URL}/api/discover").mock(side_effect=discover)

    with pytest.raises(AppError) as caught:
        await adapter.list_missing_media()

    assert route.call_count == 3
    assert login_route.call_count == 2
    assert caught.value.error_code == "UPSTREAM_PAGINATION_UNSTABLE"
    assert caught.value.retryable is True
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_later_page_first_attempt_404_reauthenticates_and_recovers(
    adapter: NextFindAdapter,
) -> None:
    login_route = respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    page_two_attempts = 0

    def discover(request: httpx.Request) -> httpx.Response:
        nonlocal page_two_attempts
        if request.url.params["page"] == "1":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": item_id, "type": "movie", "title": f"Movie {item_id}"}
                        for item_id in range(1, 101)
                    ],
                    "current_page": 1,
                    "total_pages": 2,
                    "next_cursor": None,
                },
            )
        page_two_attempts += 1
        if page_two_attempts == 1:
            return httpx.Response(404)
        return httpx.Response(
            200,
            json={
                "data": [{"id": 101, "type": "movie", "title": "Recovered"}],
                "current_page": 2,
                "total_pages": 2,
                "next_cursor": None,
            },
        )

    route = respx.get(f"{BASE_URL}/api/discover").mock(side_effect=discover)

    await adapter.authenticate()
    items, warnings = await adapter._list_missing_media_type("电影")

    assert route.call_count == 3
    assert login_route.call_count == 2
    requests = [dict(call.request.url.params) for call in route.calls]
    assert requests[1] == requests[2]
    assert requests[1]["page"] == "2"
    assert {item.tmdb_id for item in items} == {*range(1, 101), 101}
    assert not warnings
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_later_page_404_reauthentication_has_its_own_attempt_budget() -> None:
    adapter = NextFindAdapter(
        base_url=BASE_URL,
        allowed_hosts=("nextfind.example",),
        username="reader",
        password="super-secret",
        discover_page_size=100,
        stream_retry_attempts=1,
        retry_sleep=no_retry_sleep,
    )
    login_route = respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    attempt = 0

    def discover(_: httpx.Request) -> httpx.Response:
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            return httpx.Response(404)
        return httpx.Response(
            200,
            json={
                "data": [{"id": 101, "type": "movie", "title": "Recovered"}],
                "current_page": 2,
                "total_pages": 2,
                "next_cursor": None,
            },
        )

    route = respx.get(f"{BASE_URL}/api/discover").mock(side_effect=discover)

    items, warnings, _ = await adapter._read_discover_page(
        {
            "status": "未入库",
            "type": "电影",
            "page": 2,
            "page_size": 100,
            "sort": "updated_at",
        },
        page=2,
    )

    assert route.call_count == 2
    assert login_route.call_count == 1
    assert [item.tmdb_id for item in items] == [101]
    assert not warnings
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_later_page_404_after_reauthentication_is_still_an_upstream_error(
    adapter: NextFindAdapter,
) -> None:
    login_route = respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(200, json={"success": True})
    )

    def discover(request: httpx.Request) -> httpx.Response:
        if request.url.params["page"] == "1":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": item_id, "type": "movie", "title": f"Movie {item_id}"}
                        for item_id in range(1, 101)
                    ],
                    "current_page": 1,
                    "total_pages": 2,
                    "next_cursor": None,
                },
            )
        return httpx.Response(404)

    route = respx.get(f"{BASE_URL}/api/discover").mock(side_effect=discover)

    await adapter.authenticate()
    with pytest.raises(AppError) as caught:
        await adapter._list_missing_media_type("电影")

    assert route.call_count == 3
    assert login_route.call_count == 2
    assert caught.value.error_code == "UPSTREAM_PAGINATION_UNSTABLE"
    assert caught.value.retryable is True
    await adapter.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "pagination_fields",
        "expected_warning",
        "expected_current_page",
        "expected_total_pages",
    ),
    (
        (
            {"current_page": 0, "total_pages": 2, "next_cursor": "poisoned"},
            "PAGINATION_METADATA_INVALID",
            None,
            None,
        ),
        (
            {"current_page": 1, "total_pages": 0, "next_cursor": "poisoned"},
            "PAGINATION_METADATA_INVALID",
            None,
            None,
        ),
        (
            {"current_page": 3, "total_pages": 2, "next_cursor": "poisoned"},
            "PAGINATION_METADATA_CONFLICT",
            None,
            None,
        ),
    ),
)
async def test_invalid_pagination_metadata_warns_and_falls_back(
    adapter: NextFindAdapter,
    pagination_fields: dict[str, int],
    expected_warning: str,
    expected_current_page: int | None,
    expected_total_pages: int | None,
) -> None:
    response = httpx.Response(
        200,
        json={
            "data": [{"id": 1, "type": "movie", "title": "First"}],
            **pagination_fields,
        },
    )

    items, warnings, pagination = await adapter._parse_stream(response, expected_page=1)

    assert [item.tmdb_id for item in items] == [1]
    assert expected_warning in {warning.error_code for warning in warnings}
    assert pagination.current_page == expected_current_page
    assert pagination.total_pages == expected_total_pages
    assert pagination.next_cursor is None
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_conflicting_pagination_segments_warn_and_fall_back(
    adapter: NextFindAdapter,
) -> None:
    respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    route = respx.get(f"{BASE_URL}/api/discover").mock(
        return_value=httpx.Response(
            200,
            content=(
                b'{"data":[{"id":1,"type":"movie","title":"First"}],'
                b'"current_page":1,"total_pages":2}\n'
                b'{"type":"pagination","status":"complete",'
                b'"current_page":2,"total_pages":3,"next_cursor":null}\n'
            ),
            headers={"content-type": "application/x-ndjson"},
        )
    )

    await adapter.authenticate()
    items, warnings = await adapter._list_missing_media_type("电影")

    assert route.call_count == 1
    assert [item.tmdb_id for item in items] == [1]
    assert "PAGINATION_METADATA_CONFLICT" in {
        warning.error_code for warning in warnings
    }
    assert "ITEM_VALIDATION_ERROR" not in {
        warning.error_code for warning in warnings
    }
    await adapter.aclose()


@pytest.mark.asyncio
async def test_conflicting_page_metadata_discards_an_observed_cursor(
    adapter: NextFindAdapter,
) -> None:
    response = httpx.Response(
        200,
        content=(
            b'{"data":[{"id":1,"type":"movie","title":"First"}],'
            b'"current_page":1,"total_pages":2,"next_cursor":"poisoned"}\n'
            b'{"type":"pagination","status":"complete",'
            b'"current_page":2,"total_pages":2,"next_cursor":"poisoned"}\n'
        ),
        headers={"content-type": "application/x-ndjson"},
    )

    _, warnings, pagination = await adapter._parse_stream(response, expected_page=1)

    assert "PAGINATION_METADATA_CONFLICT" in {
        warning.error_code for warning in warnings
    }
    assert "ITEM_VALIDATION_ERROR" not in {
        warning.error_code for warning in warnings
    }
    assert pagination.next_cursor is None
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_final_page_cursor_conflict_warns_and_does_not_fetch_beyond_total_pages(
    adapter: NextFindAdapter,
) -> None:
    respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    route = respx.get(f"{BASE_URL}/api/discover").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [{"id": 1, "type": "movie", "title": "First"}],
                "current_page": 1,
                "total_pages": 1,
                "next_cursor": "must-not-be-followed",
            },
        )
    )

    await adapter.authenticate()
    items, warnings = await adapter._list_missing_media_type("电影")

    assert route.call_count == 1
    assert [item.tmdb_id for item in items] == [1]
    assert "PAGINATION_METADATA_CONFLICT" in {
        warning.error_code for warning in warnings
    }
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_stream_disconnect_retries_the_same_page_without_merging_partial_items(
    adapter: NextFindAdapter,
) -> None:
    respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    attempt = 0

    def discover(_: httpx.Request) -> httpx.Response:
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            return httpx.Response(
                200,
                content=(
                    b'{"data":{"id":1,"type":"movie","title":"First"},'
                    b'"next_cursor":"cursor-2"}\n'
                ),
                headers={"content-type": "application/x-ndjson"},
            )
        if attempt == 2:
            return httpx.Response(
                200,
                headers={"content-type": "application/x-ndjson"},
                stream=DisconnectingStream(
                    [
                        b'{"data":{"id":999,"type":"movie","title":"Partial sentinel"},'
                        b'"next_cursor":"poisoned-cursor"}\n'
                    ],
                    httpx.RemoteProtocolError("peer disconnected during response body"),
                ),
            )
        return httpx.Response(
            200,
            content=b'{"data":{"id":2,"type":"movie","title":"Second"}}\n',
            headers={"content-type": "application/x-ndjson"},
        )

    route = respx.get(f"{BASE_URL}/api/discover").mock(side_effect=discover)

    await adapter.authenticate()
    items, warnings = await adapter._list_missing_media_type("电影")

    assert route.call_count == 3
    requests = [dict(call.request.url.params) for call in route.calls]
    assert requests[1] == requests[2] == {
        "status": "未入库",
        "type": "电影",
        "page": "2",
        "page_size": "100",
        "sort": "updated_at",
        "cursor": "cursor-2",
    }
    assert [item.tmdb_id for item in items] == [1, 2]
    assert all(item.title != "Partial sentinel" for item in items)
    assert not warnings
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_stream_disconnect_exhaustion_is_a_retryable_network_error(
    adapter: NextFindAdapter,
) -> None:
    respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(200, json={"success": True})
    )

    def disconnect(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/x-ndjson"},
            stream=DisconnectingStream(
                [b'{"data":{"id":999,"type":"movie","title":"Partial sentinel"}}\n'],
                httpx.RemoteProtocolError("peer disconnected during response body"),
            ),
        )

    route = respx.get(f"{BASE_URL}/api/discover").mock(side_effect=disconnect)

    with pytest.raises(AppError) as caught:
        await adapter.list_missing_media()

    assert route.call_count == 3
    assert caught.value.error_code == "UPSTREAM_NETWORK_ERROR"
    assert caught.value.retryable is True
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_stream_decoding_error_exhaustion_is_a_retryable_network_error(
    adapter: NextFindAdapter,
) -> None:
    respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(200, json={"success": True})
    )

    def fail_decoding(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/x-ndjson"},
            stream=DisconnectingStream(
                [b'{"data":{"id":999,"type":"movie","title":"Partial sentinel"}}\n'],
                httpx.DecodingError("invalid compressed response body"),
            ),
        )

    route = respx.get(f"{BASE_URL}/api/discover").mock(side_effect=fail_decoding)

    with pytest.raises(AppError) as caught:
        await adapter.list_missing_media()

    assert route.call_count == 3
    assert caught.value.error_code == "UPSTREAM_NETWORK_ERROR"
    assert caught.value.retryable is True
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_stream_read_timeout_exhaustion_is_retryable(adapter: NextFindAdapter) -> None:
    respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(200, json={"success": True})
    )

    def time_out(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/x-ndjson"},
            stream=DisconnectingStream(
                [b'{"data":{"id":999,"type":"movie","title":"Partial sentinel"}}\n'],
                httpx.ReadTimeout("timed out while reading response body"),
            ),
        )

    route = respx.get(f"{BASE_URL}/api/discover").mock(side_effect=time_out)

    with pytest.raises(AppError) as caught:
        await adapter.list_missing_media()

    assert route.call_count == 3
    assert caught.value.error_code == "UPSTREAM_TIMEOUT"
    assert caught.value.retryable is True
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
@respx.mock
@pytest.mark.parametrize(
    "redirect_url",
    (
        "https://api.themoviedb.org/redirected-login",
        "https://nextfind.example:444/redirected-login",
    ),
)
async def test_login_body_is_not_forwarded_across_origins(redirect_url: str) -> None:
    broad_adapter = NextFindAdapter(
        base_url=BASE_URL,
        allowed_hosts=("nextfind.example", "api.themoviedb.org"),
        username="reader",
        password="super-secret",
    )
    respx.post(f"{BASE_URL}/api/admin/login").mock(
        return_value=httpx.Response(
            307,
            headers={"location": redirect_url},
        )
    )
    redirected = respx.post(redirect_url).mock(
        return_value=httpx.Response(200, json={"success": True})
    )

    with pytest.raises(AppError) as caught:
        await broad_adapter.authenticate()

    assert caught.value.error_code == "UPSTREAM_CROSS_ORIGIN_REDIRECT"
    assert not redirected.called
    await broad_adapter.aclose()


@pytest.mark.asyncio
async def test_default_respx_policy_blocks_unmatched_real_network(adapter: NextFindAdapter) -> None:
    with respx.mock(assert_all_mocked=True), pytest.raises(AssertionError):
        await adapter.authenticate()
    await adapter.aclose()
