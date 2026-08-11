from __future__ import annotations

import json

import httpx
import pytest

from app.adapters.media_sources.nextfind import NextFindAdapter
from app.core.config import Settings
from app.errors import AppError
from app.tools import nextfind_contract_probe as probe_module
from app.tools.nextfind_contract_probe import (
    ProbeLimits,
    run_contract_probe,
    summarize_contract_response,
)

BASE_URL = "https://nextfind.example"
PROBE_USERNAME = "contract-probe-user-private"
PROBE_PASSWORD = "contract-probe-password-private"


def _configured_settings() -> Settings:
    return Settings(
        nextfind_base_url=BASE_URL,
        nextfind_username=PROBE_USERNAME,
        nextfind_password=PROBE_PASSWORD,
        nextfind_username_file=None,
        nextfind_password_file=None,
        allowed_external_hosts=("nextfind.example",),
    )


def _field(summary, path: str):
    return next(item for item in summary.field_paths if item.path == path)


def _known_field(fields, path: str):
    return next(item for item in fields if item.path == path)


@pytest.mark.asyncio
async def test_pretty_json_first_page_reports_schema_only() -> None:
    requests: list[tuple[str, str]] = []
    response_values = (
        "PRIVATE-TITLE-ALPHA",
        "PRIVATE-TITLE-BETA",
        "PRIVATE-ORIGINAL-TITLE",
        "PRIVATE-TMDB-ID-VALUE",
        "PRIVATE-NEXT-CURSOR",
        "PRIVATE-SESSION-COOKIE",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.path == "/api/admin/login":
            assert json.loads(request.content) == {
                "username": PROBE_USERNAME,
                "password": PROBE_PASSWORD,
            }
            return httpx.Response(
                200,
                json={"success": True},
                headers={"set-cookie": f"session={response_values[-1]}; Path=/; HttpOnly"},
            )
        assert request.method == "GET"
        assert request.url.path == "/api/discover"
        assert request.url.params["status"] == "未入库"
        assert request.url.params["page"] == "1"
        assert request.url.params["page_size"] == "100"
        assert request.url.params["sort"] == "updated_at"
        pretty_payload = json.dumps(
            {
                "data": [
                    {
                        "id": "PRIVATE-SOURCE-ID-VALUE",
                        "tmdb_id": response_values[3],
                        "type": "tv",
                        "title": response_values[0],
                        "original_title": response_values[2],
                        "episode_number": 7,
                    },
                    {
                        "id": "PRIVATE-SOURCE-ID-TWO",
                        "type": "movie",
                        "title": response_values[1],
                    },
                ],
                "meta": {"next_cursor": response_values[4], "page": 1, "total": 2},
            },
            ensure_ascii=False,
            indent=2,
        ).encode()
        return httpx.Response(
            200,
            content=pretty_payload,
            headers={"content-type": "application/json; charset=utf-8"},
        )

    summary = await run_contract_probe(
        confirm_read_only=True,
        settings=_configured_settings(),
        transport=httpx.MockTransport(handler),
    )

    assert requests == [
        ("POST", "/api/admin/login"),
        ("GET", "/api/discover"),
    ]
    assert summary.response_format == "json"
    assert summary.valid_document_count == 1
    assert summary.bad_ndjson_line_count == 0
    assert _field(summary, "$.data[].title").types == ("string",)
    assert _field(summary, "$.data[].title").presence_rate == 1
    assert _field(summary, "$.data[].original_title").presence_rate == 0.5
    assert _field(summary, "$.data[].tmdb_id").presence_rate == 0.5
    assert _known_field(summary.envelope_fields, "$.data").present is True
    assert _known_field(summary.pagination_fields, "$.meta.next_cursor").present is True

    serialized = summary.model_dump_json()
    for private_value in (*response_values, PROBE_USERNAME, PROBE_PASSWORD):
        assert private_value not in serialized


@pytest.mark.asyncio
async def test_ndjson_bad_lines_and_hidden_field_names_are_counted_only() -> None:
    requests: list[str] = []
    hidden_values = (
        "PRIVATE-NDJSON-TITLE-A",
        "PRIVATE-NDJSON-TITLE-B",
        "PRIVATE-PASSWORD-VALUE",
        "PRIVATE-TOKEN-VALUE",
        "PRIVATE-COOKIE-VALUE",
        "PRIVATE-CURSOR-VALUE",
        "PRIVATE-IMDB-ID-VALUE",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/api/admin/login":
            return httpx.Response(200, json={"ok": True})
        body = b"\n".join(
            (
                json.dumps(
                    {
                        "items": [
                            {
                                "title": hidden_values[0],
                                "tmdb_id": 101,
                                "password": hidden_values[2],
                                "apiToken": hidden_values[3],
                                "bad field name": "PRIVATE-UNSAFE-FIELD-VALUE",
                            }
                        ],
                        "cursor": hidden_values[5],
                    }
                ).encode(),
                b"{broken-json-line",
                b"",
                json.dumps(
                    {
                        "items": [
                            {
                                "title": hidden_values[1],
                                "imdb_id": hidden_values[6],
                                "details": {
                                    "cookie": hidden_values[4],
                                    "available": True,
                                    "DunePrivateTitleAsKey": "PRIVATE-DYNAMIC-KEY-VALUE",
                                    "tt9876543": "PRIVATE-DYNAMIC-ID-KEY-VALUE",
                                },
                            }
                        ],
                        "cursor": None,
                    }
                ).encode(),
            )
        )
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "application/x-ndjson"},
        )

    summary = await run_contract_probe(
        confirm_read_only=True,
        settings=_configured_settings(),
        transport=httpx.MockTransport(handler),
    )

    assert requests == ["/api/admin/login", "/api/discover"]
    assert summary.response_format == "ndjson"
    assert summary.valid_document_count == 2
    assert summary.bad_ndjson_line_count == 1
    assert summary.blank_ndjson_line_count == 1
    assert summary.hidden_fields.secret_like_name_occurrences == 3
    assert summary.hidden_fields.unsafe_name_occurrences == 1
    assert summary.hidden_fields.unrecognized_name_occurrences == 2
    assert _field(summary, "$.items[].title").presence_rate == 1
    assert _known_field(summary.envelope_fields, "$.items").present is True
    assert _known_field(summary.pagination_fields, "$.cursor").present is True

    serialized = summary.model_dump_json()
    serialized_lower = serialized.casefold()
    for hidden_value in (
        *hidden_values,
        "PRIVATE-UNSAFE-FIELD-VALUE",
        "PRIVATE-DYNAMIC-KEY-VALUE",
        "PRIVATE-DYNAMIC-ID-KEY-VALUE",
    ):
        assert hidden_value not in serialized
    for hidden_name in (
        "password",
        "apitoken",
        "bad field name",
        "cookie",
        "duneprivatetitleaskey",
        "tt9876543",
    ):
        assert hidden_name not in serialized_lower


@pytest.mark.asyncio
async def test_probe_without_confirmation_never_loads_the_network() -> None:
    request_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        raise AssertionError("network must not be reached")

    with pytest.raises(AppError) as caught:
        await run_contract_probe(
            confirm_read_only=False,
            settings=_configured_settings(),
            transport=httpx.MockTransport(handler),
        )

    assert caught.value.error_code == "READ_ONLY_CONFIRMATION_REQUIRED"
    assert request_count == 0


@pytest.mark.asyncio
async def test_probe_without_credentials_never_loads_the_network() -> None:
    request_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        raise AssertionError("network must not be reached")

    settings = Settings(
        nextfind_base_url=BASE_URL,
        nextfind_username=None,
        nextfind_password=None,
        nextfind_username_file=None,
        nextfind_password_file=None,
        allowed_external_hosts=("nextfind.example",),
    )
    with pytest.raises(AppError) as caught:
        await run_contract_probe(
            confirm_read_only=True,
            settings=settings,
            transport=httpx.MockTransport(handler),
        )

    assert caught.value.error_code == "NEXTFIND_NOT_CONFIGURED"
    assert request_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "borrowed_url",
    ("https://api.themoviedb.org", "https://avistaz.to"),
)
async def test_probe_rejects_borrowing_a_global_host_before_network_or_secret_read(
    monkeypatch: pytest.MonkeyPatch,
    borrowed_url: str,
) -> None:
    request_count = 0
    private_username = "private-nextfind-user"
    private_password = "PRIVATE-NEXTFIND-PASSWORD"

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        raise AssertionError("network must not be reached")

    settings = Settings(
        nextfind_base_url=borrowed_url,
        nextfind_allowed_hosts=("nextfind.example",),
        nextfind_username=private_username,
        nextfind_password=private_password,
        allowed_external_hosts=(
            "nextfind.example",
            "api.themoviedb.org",
            "avistaz.to",
        ),
    )
    secret_read_count = 0
    original_credentials = Settings.nextfind_credentials

    def tracked_credentials(selected_settings: Settings) -> tuple[str, str] | None:
        nonlocal secret_read_count
        assert selected_settings is settings
        secret_read_count += 1
        return original_credentials(selected_settings)

    monkeypatch.setattr(Settings, "nextfind_credentials", tracked_credentials)

    with pytest.raises(AppError) as caught:
        await run_contract_probe(
            confirm_read_only=True,
            settings=settings,
            transport=httpx.MockTransport(handler),
        )

    assert caught.value.error_code == "EXTERNAL_HOST_NOT_ALLOWED"
    assert request_count == 0
    assert secret_read_count == 0
    serialized_error = f"{caught.value.message} {caught.value.details}"
    assert borrowed_url.partition("://")[2] not in serialized_error
    assert private_username not in serialized_error
    assert private_password not in serialized_error


def test_cli_fails_closed_before_loading_configuration(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_if_called() -> Settings:
        raise AssertionError("configuration must not be loaded before confirmation")

    monkeypatch.setattr(probe_module, "get_settings", fail_if_called)

    exit_code = probe_module.main([])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "status": "error",
        "error_code": "READ_ONLY_CONFIRMATION_REQUIRED",
    }


def test_schema_limits_are_reported_without_exposing_omitted_fields() -> None:
    omitted_value = "PRIVATE-OMITTED-VALUE"
    summary = summarize_contract_response(
        content_type="application/json",
        payload_bytes=json.dumps(
            {
                "data": {"details": {"metadata": {"overview": omitted_value}}},
                "items": [{"title": "VISIBLE-TYPE-ONLY"}, {"title": omitted_value}],
                "meta": {"page": 1},
                "pagination": {"total": 4},
            }
        ).encode(),
        limits=ProbeLimits(
            max_field_paths=3,
            max_fields_per_object=3,
            max_depth=1,
            max_array_items=1,
        ),
    )

    assert summary.limit_events.fields_per_object_limit == 1
    assert summary.limit_events.depth_limit >= 1
    assert summary.limit_events.array_item_limit == 1
    assert omitted_value not in summary.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", (301, 302, 303, 307, 308))
async def test_contract_probe_never_follows_login_redirects(status_code: int) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if len(requests) > 1:
            raise AssertionError("login redirect target must not be requested")
        return httpx.Response(
            status_code,
            headers={
                "location": f"{BASE_URL}/api/admin/redirected-login",
                "set-cookie": "session=PRIVATE-REDIRECT-COOKIE; Path=/; HttpOnly",
            },
        )

    with pytest.raises(AppError) as caught:
        await run_contract_probe(
            confirm_read_only=True,
            settings=_configured_settings(),
            transport=httpx.MockTransport(handler),
        )

    assert caught.value.error_code == "UPSTREAM_HTTP_ERROR"
    assert requests == [f"{BASE_URL}/api/admin/login"]
    assert "PRIVATE-REDIRECT-COOKIE" not in f"{caught.value.message} {caught.value.details}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "redirect_location",
    (
        "https://nextfind.example:444/api/discover?page=1",
        "https://nextfind.example/api/discover?page=2",
    ),
)
async def test_contract_probe_never_follows_discover_redirects(
    redirect_location: str,
) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/api/admin/login":
            return httpx.Response(
                200,
                json={"ok": True},
                headers={"set-cookie": "session=PRIVATE-REDIRECT-COOKIE; Path=/; HttpOnly"},
            )
        if len(requests) > 2:
            raise AssertionError("discover redirect must not be requested")
        return httpx.Response(302, headers={"location": redirect_location})

    with pytest.raises(AppError) as caught:
        await run_contract_probe(
            confirm_read_only=True,
            settings=_configured_settings(),
            transport=httpx.MockTransport(handler),
        )

    assert caught.value.error_code == "UPSTREAM_HTTP_ERROR"
    assert len(requests) == 2
    assert requests[0].startswith(f"{BASE_URL}/api/admin/login")
    assert requests[1].startswith(f"{BASE_URL}/api/discover?")


@pytest.mark.asyncio
async def test_authenticated_adapter_rejects_cross_origin_discover_redirect() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/api/admin/login":
            return httpx.Response(200, json={"ok": True})
        if len(requests) > 2:
            raise AssertionError("cross-origin redirect target must not be requested")
        return httpx.Response(
            307,
            headers={"location": "https://nextfind.example:444/api/discover?page=1"},
        )

    async with NextFindAdapter(
        base_url=BASE_URL,
        allowed_hosts=("nextfind.example",),
        username=PROBE_USERNAME,
        password=PROBE_PASSWORD,
        transport=httpx.MockTransport(handler),
    ) as adapter:
        with pytest.raises(AppError) as caught:
            await adapter.list_missing_media()

    assert caught.value.error_code == "UPSTREAM_CROSS_ORIGIN_REDIRECT"
    assert len(requests) == 2
    assert requests[0].startswith(f"{BASE_URL}/api/admin/login")
    assert requests[1].startswith(f"{BASE_URL}/api/discover?")


def test_response_size_limit_fails_closed() -> None:
    limits = ProbeLimits(max_response_bytes=16)
    with pytest.raises(AppError) as caught:
        summarize_contract_response(
            content_type="application/json",
            payload_bytes=b'{"title":"this response is too large"}',
            limits=limits,
        )

    assert caught.value.error_code == "UPSTREAM_RESPONSE_TOO_LARGE"
