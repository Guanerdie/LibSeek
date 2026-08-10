from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
)


class NextFindRawItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | int | None = None
    source_item_id: str | int | None = None
    tmdb_id: str | int | None = None
    type: str | None = None
    title: str
    original_title: str | None = None
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
    ) -> None:
        self.base_url = validate_external_url(base_url.rstrip("/"), allowed_hosts)
        self.allowed_hosts = allowed_hosts
        self.username = username
        self.password = password
        self.max_response_bytes = max_response_bytes
        self.max_line_bytes = max_line_bytes
        self.cookies = httpx.Cookies()
        self._authenticated = False
        self._client = httpx.AsyncClient(
            cookies=self.cookies,
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
            transport=transport,
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
            return ProbeResult(healthy=True, message="NextFind 登录验证成功")
        except AppError as exc:
            return ProbeResult(healthy=False, error_code=exc.error_code, message=exc.message)

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
                if not response.is_redirect:
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
                if current_body is not None and self._origin(redirect_url) != self._origin(
                    current_url
                ):
                    raise AppError(
                        "UPSTREAM_CROSS_ORIGIN_REDIRECT",
                        "NextFind 认证请求拒绝跨源跳转",
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
        async for chunk in response.aiter_bytes():
            result.extend(chunk)
            if len(result) > self.max_response_bytes:
                raise AppError("UPSTREAM_RESPONSE_TOO_LARGE", "NextFind 响应体超过安全限制")
        return bytes(result)

    @staticmethod
    def _require_json_content(response: httpx.Response) -> None:
        content_type = response.headers.get("content-type", "").lower()
        if "json" not in content_type:
            raise AppError("UPSTREAM_NON_JSON", "NextFind 返回了非 JSON 响应", status_code=502)

    async def authenticate(self) -> None:
        async with self._open_response(
            "POST",
            "/api/admin/login",
            json_body={"username": self.username, "password": self.password},
        ) as response:
            self._raise_for_status(response, authenticating=True)
            self._require_json_content(response)
            payload_bytes = await self._read_limited(response)
        try:
            payload = json.loads(payload_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AppError("UPSTREAM_INVALID_JSON", "NextFind 登录响应格式无效") from exc
        if not isinstance(payload, dict):
            raise AppError("UPSTREAM_INVALID_JSON", "NextFind 登录响应格式无效")
        if payload.get("success") is False or payload.get("ok") is False:
            raise AppError("AUTH_FAILED", "NextFind 登录失败", status_code=401)
        self._authenticated = True

    async def list_missing_media(self) -> MediaDiscoveryResult:
        if not self._authenticated:
            await self.authenticate()
        page = 1
        page_size = 100
        cursor: str | None = None
        seen_cursors: set[str] = set()
        seen_pages: set[tuple[str, ...]] = set()
        merged: dict[tuple[str, str, int | str], MediaItemData] = {}
        all_warnings: list[DiscoveryWarning] = []
        for _ in range(1000):
            params: dict[str, str | int] = {
                "status": "未入库",
                "page": page,
                "page_size": page_size,
                "sort": "updated_at",
            }
            if cursor is not None:
                params["cursor"] = cursor
            async with self._open_response("GET", "/api/discover", params=params) as response:
                self._raise_for_status(response)
                content_type = response.headers.get("content-type", "").lower()
                if "ndjson" not in content_type and "json" not in content_type:
                    raise AppError(
                        "UPSTREAM_NON_JSON", "NextFind 返回了非 JSON 响应", status_code=502
                    )
                items, warnings, next_cursor = await self._parse_stream(response)
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
                identity: int | str = (
                    item.tmdb_id if item.tmdb_id is not None else item.source_item_id
                )
                merged[(item.source, item.media_type.value, identity)] = item
            if next_cursor:
                if next_cursor in seen_cursors:
                    all_warnings.append(
                        DiscoveryWarning(
                            error_code="CURSOR_LOOP_ISOLATED",
                            message="检测到重复游标，已安全停止继续读取",
                        )
                    )
                    break
                seen_cursors.add(next_cursor)
                cursor = next_cursor
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
        return MediaDiscoveryResult(items=list(merged.values()), warnings=all_warnings)

    async def _parse_stream(
        self, response: httpx.Response
    ) -> tuple[list[MediaItemData], list[DiscoveryWarning], str | None]:
        buffer = bytearray()
        total = 0
        line_number = 0
        records: list[MediaItemData] = []
        warnings: list[DiscoveryWarning] = []
        next_cursor: str | None = None

        async def consume(raw_line: bytes) -> None:
            nonlocal line_number, next_cursor
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
            discovered_cursor = self._extract_next_cursor(payload)
            if discovered_cursor:
                next_cursor = discovered_cursor
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

        async for chunk in response.aiter_bytes():
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
        return records, warnings, next_cursor

    @staticmethod
    def _extract_next_cursor(payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        candidate = payload.get("next_cursor")
        metadata = payload.get("meta")
        if candidate is None and isinstance(metadata, dict):
            candidate = metadata.get("next_cursor") or metadata.get("cursor")
        if candidate is None and ("data" in payload or "items" in payload):
            candidate = payload.get("cursor")
        if not isinstance(candidate, (str, int)) or candidate == "":
            return None
        return str(candidate)

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
