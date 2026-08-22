from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.adapters.base import MediaSourceAdapter
from app.core.episodes import EpisodeMatrix, normalize_episode_matrix
from app.core.security import validate_external_url
from app.errors import AppError
from app.models.enums import IdentityConfidence, MediaType, MetadataStatus
from app.schemas.adapters import (
    AdapterManifest,
    DiscoveryWarning,
    LibraryDetails,
    MediaDiscoveryResult,
    MediaItemData,
    ProbeResult,
    normalize_country_codes,
)

_DISCOVER_DATA_KEYS = frozenset({"data", "items", "list"})
_DISCOVER_CONTROL_KEYS = frozenset(
    {
        "type",
        "status",
        "current_page",
        "total_pages",
        "page_size",
        "total_results",
        "next_cursor",
    }
)
_MAX_ORIGIN_COUNTRY_JSON_CHARS = 1024
_MAX_ORIGIN_COUNTRY_CODES = 64
_MISSING_MEDIA_TYPES = ("电影", "电视剧")
_DISCOVER_PAGE_SIZE = 500


def _normalize_origin_country(value: object) -> list[str] | None:
    if not isinstance(value, str):
        return normalize_country_codes(value)

    raw = value.strip()
    direct = normalize_country_codes(raw)
    if direct is not None:
        return direct
    if (
        not raw.startswith("[")
        or not raw.endswith("]")
        or len(raw) > _MAX_ORIGIN_COUNTRY_JSON_CHARS
    ):
        return None
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, RecursionError):
        return None
    if (
        not isinstance(decoded, list)
        or len(decoded) > _MAX_ORIGIN_COUNTRY_CODES
        or not all(isinstance(item, str) for item in decoded)
    ):
        return None
    return normalize_country_codes(decoded)


@dataclass(frozen=True, slots=True)
class NextFindPaginationState:
    current_page: int | None = None
    total_pages: int | None = None
    next_cursor: str | None = None
    status: str | None = None


class NextFindRawItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | int | None = None
    source_item_id: str | int | None = None
    tmdb_id: str | int | None = None
    type: str | None = None
    title: str
    original_title: str | None = None
    country_codes: list[str] | None = None
    year: str | int | None = None
    poster: str | None = None
    local_episodes: int | None = Field(default=None, ge=0)
    local_episode_matrix: EpisodeMatrix | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "local_episode_matrix",
            "existing_episode_matrix",
            "existing_episodes",
            "localEpisodeMatrix",
            "existingEpisodes",
        ),
    )
    total_episodes: int | None = Field(default=None, ge=0)
    aired_episodes: int | None = Field(default=None, ge=0)
    missing_episodes: list[str] | None = None

    @field_validator("local_episode_matrix", mode="before")
    @classmethod
    def normalize_local_episode_matrix(cls, value: object) -> EpisodeMatrix | None:
        return normalize_episode_matrix(value)

    @model_validator(mode="before")
    @classmethod
    def normalize_keys(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        result = dict(value)
        aliases = {
            "media_type": "type",
            "raw_type": "type",
            "name": "title",
            "original_name": "original_title",
            "release_year": "year",
            "poster_path": "poster",
        }
        for source, target in aliases.items():
            if target not in result and source in result:
                result[target] = result[source]
        country_codes: list[str] = []
        for key in (
            "country_codes",
            "origin_country",
            "country",
            "countries",
            "production_countries",
        ):
            value = result.get(key)
            normalized = (
                _normalize_origin_country(value)
                if key == "origin_country"
                else normalize_country_codes(value)
            )
            if normalized is not None:
                country_codes.extend(normalized)
        result["country_codes"] = normalize_country_codes(country_codes)
        return result


class NextFindLibraryPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    local_episodes: int | None = Field(default=None, ge=0)
    local_episode_matrix: EpisodeMatrix | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "local_episode_matrix",
            "existing_episode_matrix",
            "existing_episodes",
            "localEpisodeMatrix",
            "existingEpisodes",
        ),
    )
    total_episodes: int | None = Field(default=None, ge=0)
    aired_episodes: int | None = Field(default=None, ge=0)
    missing_episodes: list[str] | None = None

    @field_validator("local_episode_matrix", mode="before")
    @classmethod
    def normalize_local_episode_matrix(cls, value: object) -> EpisodeMatrix | None:
        return normalize_episode_matrix(value)


class NextFindAdapter(MediaSourceAdapter):
    def __init__(
        self,
        *,
        base_url: str,
        allowed_hosts: tuple[str, ...],
        username: str,
        password: str,
        transport: httpx.AsyncBaseTransport | None = None,
        max_response_bytes: int = 10 * 1024 * 1024,
        max_line_bytes: int = 1024 * 1024,
        connect_timeout: float = 5.0,
        read_timeout: float = 30.0,
        discover_page_size: int = _DISCOVER_PAGE_SIZE,
        stream_retry_attempts: int = 3,
        retry_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.base_url = validate_external_url(base_url.rstrip("/"), allowed_hosts)
        self.allowed_hosts = allowed_hosts
        self.username = username
        self.password = password
        self.max_response_bytes = max_response_bytes
        self.max_line_bytes = max_line_bytes
        self.discover_page_size = max(1, min(discover_page_size, _DISCOVER_PAGE_SIZE))
        self.stream_retry_attempts = max(1, stream_retry_attempts)
        self.retry_sleep = retry_sleep
        self.cookies = httpx.Cookies()
        self._authenticated = False
        self._client = httpx.AsyncClient(
            cookies=self.cookies,
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            headers={"Accept": "application/json, application/x-ndjson"},
        )
        # httpx copies the supplied cookie container; expose the actual isolated client jar.
        self.cookies = self._client.cookies

    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            id="nextfind",
            name="NextFind",
            adapter_type="media_source",
            version="1.0",
            enabled=True,
            mode="READ_ONLY",
            description="只读取未入库影视和本地媒体信息",
            capabilities={
                "list_missing_media": True,
                "library_details": True,
                "write_operations": False,
            },
        )

    async def __aenter__(self) -> NextFindAdapter:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def probe(self) -> ProbeResult:
        try:
            await self.authenticate()
            await self._probe_missing_media_endpoint()
            return ProbeResult(
                healthy=True,
                message="NextFind 登录与未入库列表验证成功",
            )
        except AppError as exc:
            return ProbeResult(healthy=False, error_code=exc.error_code, message=exc.message)

    async def _probe_missing_media_endpoint(self) -> None:
        params: dict[str, str | int] = {
            "status": "未入库",
            "page": 1,
            "page_size": 1,
            "sort": "updated_at",
        }
        async with self._open_response(
            "GET",
            "/api/discover",
            params=params,
            allow_redirects=False,
        ) as response:
            self._raise_for_status(response)
            self._require_json_content(response)
            content_type = response.headers.get("content-type", "").lower()
            payload_bytes = await self._read_limited(response)
        self._validate_discover_probe_payload(content_type, payload_bytes)

    def _validate_discover_probe_payload(
        self, content_type: str, payload_bytes: bytes
    ) -> None:
        try:
            if "ndjson" in content_type:
                lines = payload_bytes.splitlines()
                if any(len(line) > self.max_line_bytes for line in lines):
                    raise AppError(
                        "UPSTREAM_RESPONSE_TOO_LARGE",
                        "NextFind 未入库列表响应行超过安全限制",
                    )
                found_data_segment = False
                for line in lines:
                    if line.strip():
                        payload = json.loads(line)
                        if self._is_discover_probe_data_segment(payload):
                            found_data_segment = True
                        elif not self._is_discover_probe_control_segment(payload):
                            self._raise_invalid_discover_probe_payload()
                if not found_data_segment:
                    self._raise_invalid_discover_probe_payload()
                return
            if not self._is_discover_probe_data_segment(json.loads(payload_bytes)):
                self._raise_invalid_discover_probe_payload()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AppError(
                "UPSTREAM_INVALID_JSON",
                "NextFind 未入库列表响应格式无效",
                status_code=502,
            ) from exc

    @staticmethod
    def _is_discover_probe_data_segment(payload: Any) -> bool:
        if isinstance(payload, list):
            return all(isinstance(item, dict) for item in payload)
        if not isinstance(payload, dict):
            return False
        if not payload.keys() <= _DISCOVER_DATA_KEYS | _DISCOVER_CONTROL_KEYS:
            return False
        roots = [payload[key] for key in _DISCOVER_DATA_KEYS if key in payload]
        return bool(roots) and all(
            isinstance(root, list) and all(isinstance(item, dict) for item in root)
            for root in roots
        ) and NextFindAdapter._has_valid_discover_probe_control_fields(payload)

    @staticmethod
    def _is_discover_probe_control_segment(payload: Any) -> bool:
        if not isinstance(payload, dict) or not payload:
            return False
        if not payload.keys() <= _DISCOVER_CONTROL_KEYS:
            return False

        return NextFindAdapter._has_valid_discover_probe_control_fields(payload)

    @staticmethod
    def _has_valid_discover_probe_control_fields(payload: dict[Any, Any]) -> bool:
        for key in ("type", "status"):
            if key in payload and (
                not isinstance(payload[key], str) or not payload[key].strip()
            ):
                return False
        if "total_pages" in payload and not NextFindAdapter._is_non_negative_int(
            payload["total_pages"]
        ):
            return False
        if "current_page" in payload and (
            not NextFindAdapter._is_non_negative_int(payload["current_page"])
            or payload["current_page"] == 0
        ):
            return False
        if "page_size" in payload and (
            not NextFindAdapter._is_non_negative_int(payload["page_size"])
            or payload["page_size"] == 0
        ):
            return False
        if "total_results" in payload and not NextFindAdapter._is_non_negative_int(
            payload["total_results"]
        ):
            return False
        if "next_cursor" in payload:
            cursor = payload["next_cursor"]
            if cursor is not None and not (
                isinstance(cursor, str)
                or NextFindAdapter._is_non_negative_int(cursor)
            ):
                return False
        return True

    @staticmethod
    def _is_non_negative_int(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    @staticmethod
    def _raise_invalid_discover_probe_payload() -> None:
        raise AppError(
            "UPSTREAM_INVALID_JSON",
            "NextFind 未入库列表响应格式无效",
            status_code=502,
        )

    @staticmethod
    def _origin(url: str) -> tuple[str, str, int]:
        parsed = urlparse(url)
        try:
            port = parsed.port
        except ValueError as exc:
            raise AppError(
                "INVALID_EXTERNAL_URL", "NextFind 地址格式无效", status_code=400
            ) from exc
        return parsed.scheme.lower(), (parsed.hostname or "").lower(), port or 443

    @asynccontextmanager
    async def _open_response(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        json_body: dict[str, str] | None = None,
        allow_redirects: bool = True,
    ) -> AsyncIterator[httpx.Response]:
        current_url = urljoin(f"{self.base_url}/", path.lstrip("/"))
        current_method = method
        current_body = json_body
        current_params = params
        response: httpx.Response | None = None
        try:
            for _ in range(4):
                validate_external_url(current_url, self.allowed_hosts)
                request = self._client.build_request(
                    current_method, current_url, params=current_params, json=current_body
                )
                try:
                    response = await self._client.send(request, stream=True)
                except httpx.TimeoutException as exc:
                    raise AppError(
                        "UPSTREAM_TIMEOUT",
                        "NextFind 请求超时",
                        status_code=504,
                        retryable=True,
                    ) from exc
                except httpx.HTTPError as exc:
                    raise AppError(
                        "UPSTREAM_NETWORK_ERROR",
                        "NextFind 网络请求失败",
                        status_code=502,
                        retryable=True,
                    ) from exc
                if not response.is_redirect or not allow_redirects:
                    yield response
                    return
                location = response.headers.get("location")
                redirect_status = response.status_code
                await response.aclose()
                response = None
                if not location:
                    raise AppError("UPSTREAM_INVALID_REDIRECT", "NextFind 返回了无效跳转")
                redirect_url = urljoin(current_url, location)
                validate_external_url(redirect_url, self.allowed_hosts)
                carries_authenticated_state = self._authenticated or bool(self.cookies)
                if (
                    current_body is not None or carries_authenticated_state
                ) and self._origin(redirect_url) != self._origin(current_url):
                    raise AppError(
                        "UPSTREAM_CROSS_ORIGIN_REDIRECT",
                        "NextFind 请求拒绝跨源跳转",
                        status_code=502,
                    )
                current_url = redirect_url
                current_params = None
                if redirect_status in {301, 302, 303}:
                    current_method, current_body = "GET", None
            raise AppError("UPSTREAM_TOO_MANY_REDIRECTS", "NextFind 跳转次数过多")
        finally:
            if response is not None:
                await response.aclose()

    @staticmethod
    def _raise_for_status(response: httpx.Response, *, authenticating: bool = False) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        if status == 401:
            code = "AUTH_FAILED" if authenticating else "AUTH_EXPIRED"
            message = "NextFind 登录失败" if authenticating else "NextFind 登录已失效"
            raise AppError(code, message, status_code=401)
        if status == 403:
            raise AppError("AUTH_FORBIDDEN", "NextFind 拒绝了当前会话", status_code=403)
        if status == 404 and authenticating:
            raise AppError(
                "UPSTREAM_AUTH_UNAVAILABLE",
                "NextFind 登录服务暂时不可用",
                status_code=502,
                retryable=True,
            )
        if status == 429:
            raise AppError(
                "UPSTREAM_RATE_LIMITED",
                "NextFind 请求过于频繁，请稍后重试",
                status_code=429,
                retryable=True,
            )
        if status >= 500:
            raise AppError(
                "UPSTREAM_UNAVAILABLE",
                "NextFind 服务暂时不可用",
                status_code=502,
                retryable=True,
            )
        raise AppError("UPSTREAM_HTTP_ERROR", "NextFind 返回了无法处理的状态", status_code=502)

    async def _read_limited(self, response: httpx.Response) -> bytes:
        result = bytearray()
        async for chunk in self._iter_response_bytes(response):
            result.extend(chunk)
            if len(result) > self.max_response_bytes:
                raise AppError("UPSTREAM_RESPONSE_TOO_LARGE", "NextFind 响应体超过安全限制")
        return bytes(result)

    @staticmethod
    async def _iter_response_bytes(response: httpx.Response) -> AsyncIterator[bytes]:
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        except httpx.TimeoutException as exc:
            raise AppError(
                "UPSTREAM_TIMEOUT",
                "NextFind 响应读取超时",
                status_code=504,
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise AppError(
                "UPSTREAM_NETWORK_ERROR",
                "NextFind 响应读取中断",
                status_code=502,
                retryable=True,
            ) from exc

    @staticmethod
    def _require_json_content(response: httpx.Response) -> None:
        content_type = response.headers.get("content-type", "").lower()
        if "json" not in content_type:
            raise AppError("UPSTREAM_NON_JSON", "NextFind 返回了非 JSON 响应", status_code=502)

    async def authenticate(
        self,
        *,
        allow_redirects: bool = True,
        retry_attempts: int | None = None,
    ) -> None:
        self._authenticated = False
        attempts = (
            self.stream_retry_attempts
            if retry_attempts is None
            else max(1, retry_attempts)
        )
        for attempt in range(attempts):
            try:
                async with self._open_response(
                    "POST",
                    "/api/admin/login",
                    json_body={"username": self.username, "password": self.password},
                    allow_redirects=allow_redirects,
                ) as response:
                    self._raise_for_status(response, authenticating=True)
                    self._require_json_content(response)
                    payload_bytes = await self._read_limited(response)
                try:
                    payload = json.loads(payload_bytes)
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise AppError(
                        "UPSTREAM_INVALID_JSON", "NextFind 登录响应格式无效"
                    ) from exc
                if not isinstance(payload, dict):
                    raise AppError("UPSTREAM_INVALID_JSON", "NextFind 登录响应格式无效")
                if payload.get("success") is False or payload.get("ok") is False:
                    raise AppError("AUTH_FAILED", "NextFind 登录失败", status_code=401)
                self._authenticated = True
                return
            except AppError as exc:
                self._authenticated = False
                if not exc.retryable or attempt == attempts - 1:
                    raise
                await self.retry_sleep(0.25 * (2**attempt))
        raise RuntimeError("unreachable")

    async def list_missing_media(self) -> MediaDiscoveryResult:
        merged: dict[tuple[str, str, int | str], MediaItemData] = {}
        all_warnings: list[DiscoveryWarning] = []
        for index, media_type in enumerate(_MISSING_MEDIA_TYPES):
            if not self._authenticated or index > 0:
                await self.authenticate()
            items, warnings = await self._list_missing_media_type(media_type)
            all_warnings.extend(warnings)
            for item in items:
                self._merge_discovered_item(merged, item)
        return MediaDiscoveryResult(items=list(merged.values()), warnings=all_warnings)

    async def _list_missing_media_type(
        self, media_type: str
    ) -> tuple[list[MediaItemData], list[DiscoveryWarning]]:
        page = 1
        page_size = self.discover_page_size
        cursor: str | None = None
        seen_cursors: set[str] = set()
        seen_pages: set[tuple[str, ...]] = set()
        merged: dict[tuple[str, str, int | str], MediaItemData] = {}
        all_warnings: list[DiscoveryWarning] = []
        for _ in range(1000):
            params: dict[str, str | int] = {
                "status": "未入库",
                "type": media_type,
                "page": page,
                "page_size": page_size,
                "sort": "updated_at",
            }
            if cursor is not None:
                params["cursor"] = cursor
            items, warnings, pagination = await self._read_discover_page(params, page=page)
            all_warnings.extend(warnings)
            fingerprint = tuple(item.source_item_id for item in items)
            if fingerprint and fingerprint in seen_pages:
                all_warnings.append(
                    DiscoveryWarning(
                        error_code="PAGINATION_LOOP_ISOLATED",
                        message="检测到重复分页，已安全停止继续读取",
                    )
                )
                break
            seen_pages.add(fingerprint)
            for item in items:
                self._merge_discovered_item(merged, item)
            if pagination.next_cursor:
                if pagination.next_cursor in seen_cursors:
                    all_warnings.append(
                        DiscoveryWarning(
                            error_code="CURSOR_LOOP_ISOLATED",
                            message="检测到重复游标，已安全停止继续读取",
                        )
                    )
                    break
                seen_cursors.add(pagination.next_cursor)
                cursor = pagination.next_cursor
                page += 1
                continue
            if (
                pagination.total_pages is not None
                and page >= pagination.total_pages
            ):
                break
            if pagination.total_pages is not None:
                cursor = None
                page += 1
                continue
            if len(items) < page_size:
                break
            cursor = None
            page += 1
        else:
            all_warnings.append(
                DiscoveryWarning(
                    error_code="PAGINATION_LIMIT_REACHED",
                    message="发现页数达到安全上限，已停止继续读取",
                )
            )
        return list(merged.values()), all_warnings

    @staticmethod
    def _merge_discovered_item(
        merged: dict[tuple[str, str, int | str], MediaItemData],
        item: MediaItemData,
    ) -> None:
        identity: int | str = (
            item.tmdb_id if item.tmdb_id is not None else item.source_item_id
        )
        key = (item.source, item.media_type.value, identity)
        previous = merged.get(key)
        if (
            previous is not None
            and previous.country_codes is not None
            and item.country_codes is None
        ):
            item = item.model_copy(update={"country_codes": previous.country_codes})
        merged[key] = item

    async def _read_discover_page(
        self, params: dict[str, str | int], *, page: int
    ) -> tuple[list[MediaItemData], list[DiscoveryWarning], NextFindPaginationState]:
        stream_failed_after_success = False
        reauthenticated_after_404 = False
        attempt = 0
        max_attempts = self.stream_retry_attempts
        while attempt < max_attempts:
            attempt += 1
            should_reauthenticate = False
            try:
                async with self._open_response(
                    "GET", "/api/discover", params=params
                ) as response:
                    if response.status_code == 404 and (
                        page > 1 or stream_failed_after_success
                    ):
                        if reauthenticated_after_404:
                            raise AppError(
                                "UPSTREAM_PAGINATION_UNSTABLE",
                                "NextFind 分页状态暂时不稳定，请稍后重试",
                                status_code=502,
                                retryable=True,
                            )
                        should_reauthenticate = True
                    else:
                        self._raise_for_status(response)
                        self._require_json_content(response)
                        try:
                            return await self._parse_stream(response, expected_page=page)
                        except AppError as exc:
                            if exc.error_code in {
                                "UPSTREAM_TIMEOUT",
                                "UPSTREAM_NETWORK_ERROR",
                            }:
                                stream_failed_after_success = True
                            raise
            except AppError as exc:
                can_retry = exc.retryable and exc.error_code in {
                    "UPSTREAM_TIMEOUT",
                    "UPSTREAM_NETWORK_ERROR",
                }
                if not can_retry or attempt >= max_attempts:
                    raise
                await self.retry_sleep(0.25 * (2 ** (attempt - 1)))
                continue
            if should_reauthenticate:
                await self.authenticate()
                reauthenticated_after_404 = True
                max_attempts += 1
        raise RuntimeError("unreachable")

    async def _parse_stream(
        self, response: httpx.Response, *, expected_page: int
    ) -> tuple[list[MediaItemData], list[DiscoveryWarning], NextFindPaginationState]:
        buffer = bytearray()
        total = 0
        line_number = 0
        records: list[MediaItemData] = []
        warnings: list[DiscoveryWarning] = []
        current_pages: set[int] = set()
        total_pages_values: set[int] = set()
        next_cursors: set[str | None] = set()
        statuses: list[str] = []
        invalid_pagination_fields: set[str] = set()

        def observe_pagination(payload: Any) -> None:
            if not isinstance(payload, dict):
                return
            envelopes = [payload]
            for key in ("meta", "pagination", "page_info", "pageInfo"):
                nested = payload.get(key)
                if isinstance(nested, dict):
                    envelopes.append(nested)

            for envelope in envelopes:
                current_key = (
                    "current_page"
                    if "current_page" in envelope
                    else "page" if envelope is not payload and "page" in envelope else None
                )
                if current_key is not None:
                    value = envelope[current_key]
                    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                        current_pages.add(value)
                    else:
                        invalid_pagination_fields.add("current_page")

                if "total_pages" in envelope:
                    value = envelope["total_pages"]
                    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                        total_pages_values.add(value)
                    else:
                        invalid_pagination_fields.add("total_pages")

                cursor_key = (
                    "next_cursor"
                    if "next_cursor" in envelope
                    else "cursor" if envelope is not payload and "cursor" in envelope else None
                )
                if cursor_key is not None:
                    value = envelope[cursor_key]
                    if value is None:
                        next_cursors.add(None)
                    elif (
                        isinstance(value, (str, int))
                        and not isinstance(value, bool)
                        and str(value)
                    ):
                        next_cursors.add(str(value))
                    else:
                        invalid_pagination_fields.add("next_cursor")

                if "status" in envelope:
                    value = envelope["status"]
                    if isinstance(value, str) and value.strip():
                        statuses.append(value.strip())
                    else:
                        invalid_pagination_fields.add("status")

        async def consume(raw_line: bytes) -> None:
            nonlocal line_number
            line_number += 1
            if not raw_line.strip():
                return
            if len(raw_line) > self.max_line_bytes:
                warnings.append(
                    DiscoveryWarning(
                        line_number=line_number,
                        error_code="NDJSON_LINE_TOO_LARGE",
                        message="已隔离超出大小限制的数据行",
                    )
                )
                return
            try:
                payload = json.loads(raw_line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                warnings.append(
                    DiscoveryWarning(
                        line_number=line_number,
                        error_code="NDJSON_BAD_LINE",
                        message="已隔离损坏的数据行",
                    )
                )
                return
            observe_pagination(payload)
            if self._is_discover_control_segment(payload):
                return
            for raw_item in self._unwrap_payload(payload):
                try:
                    records.append(self._normalize(NextFindRawItem.model_validate(raw_item)))
                except (ValidationError, ValueError):
                    warnings.append(
                        DiscoveryWarning(
                            line_number=line_number,
                            error_code="ITEM_VALIDATION_ERROR",
                            message="已隔离字段不完整的数据项",
                        )
                    )

        async for chunk in self._iter_response_bytes(response):
            total += len(chunk)
            if total > self.max_response_bytes:
                raise AppError("UPSTREAM_RESPONSE_TOO_LARGE", "NextFind 响应体超过安全限制")
            buffer.extend(chunk)
            while b"\n" in buffer:
                raw_line, _, remainder = buffer.partition(b"\n")
                buffer = bytearray(remainder)
                await consume(bytes(raw_line).rstrip(b"\r"))
        if buffer:
            await consume(bytes(buffer).rstrip(b"\r"))

        conflicting_fields: set[str] = set()
        if len(current_pages) > 1:
            conflicting_fields.add("current_page")
        if len(total_pages_values) > 1:
            conflicting_fields.add("total_pages")
        if len(next_cursors) > 1:
            conflicting_fields.add("next_cursor")
        if invalid_pagination_fields:
            warnings.append(
                DiscoveryWarning(
                    error_code="PAGINATION_METADATA_INVALID",
                    message="NextFind 部分分页元数据无效，已忽略无效字段",
                )
            )
        if conflicting_fields:
            warnings.append(
                DiscoveryWarning(
                    error_code="PAGINATION_METADATA_CONFLICT",
                    message="NextFind 部分分页元数据不一致，已忽略冲突字段",
                )
            )

        page_metadata_unusable = bool(
            {"current_page", "total_pages"} & invalid_pagination_fields
        ) or bool({"current_page", "total_pages"} & conflicting_fields)
        current_page = (
            next(iter(current_pages))
            if len(current_pages) == 1
            and not page_metadata_unusable
            else None
        )
        total_pages = (
            next(iter(total_pages_values))
            if len(total_pages_values) == 1
            and not page_metadata_unusable
            else None
        )
        next_cursor = (
            next(iter(next_cursors))
            if len(next_cursors) == 1
            and not page_metadata_unusable
            and "next_cursor" not in invalid_pagination_fields
            and "next_cursor" not in conflicting_fields
            else None
        )
        pagination_relationship_conflict = (
            current_page is not None and current_page != expected_page
        ) or (
            current_page is not None
            and total_pages is not None
            and total_pages > 0
            and current_page > total_pages
        )
        if pagination_relationship_conflict:
            warnings.append(
                DiscoveryWarning(
                    error_code="PAGINATION_METADATA_CONFLICT",
                    message="NextFind 分页页码关系不一致，已回退到安全的条数判断",
                )
            )
            current_page = None
            total_pages = None
            next_cursor = None
        if next_cursor is not None and total_pages is not None and expected_page >= total_pages:
            warnings.append(
                DiscoveryWarning(
                    error_code="PAGINATION_METADATA_CONFLICT",
                    message="NextFind 终页仍返回游标，已按终页安全停止",
                )
            )
            next_cursor = None

        return (
            records,
            warnings,
            NextFindPaginationState(
                current_page=current_page,
                total_pages=total_pages,
                next_cursor=next_cursor,
                status=statuses[-1] if statuses else None,
            ),
        )

    @staticmethod
    def _is_discover_control_segment(payload: Any) -> bool:
        if not isinstance(payload, dict) or any(
            key in payload for key in _DISCOVER_DATA_KEYS
        ):
            return False
        envelope_keys = {"meta", "pagination", "page_info", "pageInfo"}
        if not payload.keys() <= _DISCOVER_CONTROL_KEYS | envelope_keys:
            return False
        return bool(
            payload.keys()
            & (
                _DISCOVER_CONTROL_KEYS
                | envelope_keys
            )
        )

    @staticmethod
    def _unwrap_payload(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []
        data = payload.get("data", payload.get("items", payload))
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            return [data]
        return []

    @staticmethod
    def _positive_int(value: str | int | None) -> int | None:
        try:
            result = int(value) if value is not None else None
        except (TypeError, ValueError):
            return None
        return result if result is not None and result > 0 else None

    @staticmethod
    def _media_type(raw_type: str | None) -> MediaType:
        normalized = (raw_type or "").strip().lower()
        if normalized in {"movie", "film", "电影"}:
            return MediaType.MOVIE
        if normalized in {"tv", "series", "电视剧", "剧集"}:
            return MediaType.TV
        raise ValueError("unsupported media type")

    def _normalize(self, raw: NextFindRawItem) -> MediaItemData:
        media_type = self._media_type(raw.type)
        tmdb_id = self._positive_int(raw.id) or self._positive_int(raw.tmdb_id)
        source_value = raw.source_item_id or raw.id
        if source_value is not None:
            source_item_id = f"nextfind:{source_value}"
        elif tmdb_id is not None:
            source_item_id = f"nextfind:tmdb:{tmdb_id}"
        else:
            identity = (
                f"{media_type.value}|{raw.title.strip()}|"
                f"{raw.original_title or ''}|{raw.year or ''}"
            )
            digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
            source_item_id = f"nextfind:temporary:{digest}"
        year = self._positive_int(raw.year)
        now = datetime.now(UTC)
        confidence = (
            IdentityConfidence.HIGH
            if tmdb_id is not None
            else IdentityConfidence.NEEDS_CONFIRMATION
        )
        metadata = (
            MetadataStatus.RESOLVED if tmdb_id is not None else MetadataStatus.NEEDS_CONFIRMATION
        )
        return MediaItemData(
            source="nextfind",
            source_item_id=source_item_id,
            media_type=media_type,
            tmdb_id=tmdb_id,
            title=raw.title.strip(),
            original_title=raw.original_title,
            country_codes=raw.country_codes,
            year=year,
            poster_path=raw.poster,
            raw_type=raw.type,
            local_episodes=raw.local_episodes,
            local_episode_matrix=raw.local_episode_matrix,
            total_episodes=raw.total_episodes,
            aired_episodes=raw.aired_episodes,
            missing_episodes=raw.missing_episodes,
            identity_confidence=confidence,
            metadata_status=metadata,
            discovered_at=now,
            updated_at=now,
        )

    async def get_library_details(
        self, media_type: MediaType, tmdb_id: int
    ) -> LibraryDetails:
        if not self._authenticated:
            await self.authenticate()
        async with self._open_response(
            "GET",
            "/api/local_library",
            params={"type": media_type.value, "tmdb_id": tmdb_id},
        ) as response:
            self._raise_for_status(response)
            self._require_json_content(response)
            raw = await self._read_limited(response)
        try:
            payload: Any = json.loads(raw)
            if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
                payload = payload["data"]
            details = NextFindLibraryPayload.model_validate(payload)
            return LibraryDetails(
                tmdb_id=tmdb_id,
                media_type=media_type,
                local_episodes=details.local_episodes,
                local_episode_matrix=details.local_episode_matrix,
                total_episodes=details.total_episodes,
                aired_episodes=details.aired_episodes,
                missing_episodes=details.missing_episodes,
            )
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as exc:
            raise AppError("UPSTREAM_VALIDATION_ERROR", "NextFind 媒体详情格式无效") from exc
