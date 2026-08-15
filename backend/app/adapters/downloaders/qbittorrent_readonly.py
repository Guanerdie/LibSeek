from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Collection, Sequence
from itertools import pairwise
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import ValidationError

from app.adapters.base import ReadOnlyDownloaderAdapter
from app.errors import AppError
from app.schemas.adapters import AdapterManifest
from app.schemas.qbittorrent import QbCategory, QbTorrent, QbTorrentFile


class QbittorrentReadOnlyAdapter(ReadOnlyDownloaderAdapter):
    _INFO_HASH = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")
    _TORRENT_PAGE_SIZE = 500
    _TORRENT_MIN_PAGE_SIZE = 2
    _TORRENT_MAX_ITEMS = 100_000
    _HASH_QUERY_BATCH_SIZE = 75
    _RECENT_TORRENT_MAX_ITEMS = 500
    _FORCED_UP_PAGE_SIZE = 100
    _FORCED_UP_MAX_ITEMS = 500
    _ALLOWED_REQUESTS = frozenset(
        {
            ("POST", "/api/v2/auth/login"),
            ("GET", "/api/v2/app/version"),
            ("GET", "/api/v2/app/webapiVersion"),
            ("GET", "/api/v2/torrents/info"),
            ("GET", "/api/v2/torrents/files"),
            ("GET", "/api/v2/torrents/categories"),
        }
    )

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        allowed_hosts: tuple[str, ...],
        allow_insecure_http: bool = False,
        connect_timeout: float = 5,
        read_timeout: float = 30,
        max_response_bytes: int = 10 * 1024 * 1024,
        transport: httpx.AsyncBaseTransport | None = None,
        before_request: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._base_url = self._validate_url(
            base_url.rstrip("/"), allowed_hosts, allow_insecure_http
        )
        if not username or not password:
            raise AppError("QB_NOT_CONFIGURED", "qBittorrent 运行时凭据未配置", status_code=409)
        self._username = username
        self._password = password
        self._authenticated = False
        self._max_response_bytes = max(1, max_response_bytes)
        self._before_request = before_request
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
            transport=transport,
            follow_redirects=False,
            trust_env=False,
            headers={
                "Accept": "application/json, text/plain;q=0.9",
                "Accept-Encoding": "identity",
            },
        )

    def set_before_request_guard(
        self, guard: Callable[[], Awaitable[None]] | None
    ) -> None:
        self._before_request = guard

    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            id="qbittorrent-read-only",
            name="qBittorrent",
            adapter_type="downloader",
            version="1.0",
            enabled=True,
            mode="LIVE_READ_ONLY",
            description="SID 会话只读状态、版本、种子、文件和分类查询",
            capabilities={
                "login": True,
                "version": True,
                "web_api_version": True,
                "list_torrents": True,
                "list_files": True,
                "list_categories": True,
                "write_operations": False,
            },
        )

    async def __aenter__(self) -> QbittorrentReadOnlyAdapter:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        self._clear_session()
        await self._client.aclose()

    async def authenticate(self) -> None:
        self._clear_session()
        try:
            response = await self._request(
                "POST",
                "/api/v2/auth/login",
                require_auth=False,
                data={"username": self._username, "password": self._password},
            )
            if self._looks_like_html(response):
                raise AppError(
                    "QB_RESPONSE_INVALID",
                    "qBittorrent 登录返回了 HTML 页面",
                    status_code=502,
                )
            if response.text.strip() != "Ok.":
                raise AppError("QB_AUTH_FAILED", "qBittorrent 登录失败", status_code=401)
            if not self._client.cookies.get("SID"):
                raise AppError(
                    "QB_RESPONSE_INVALID", "qBittorrent 登录未返回 SID", status_code=502
                )
            self._authenticated = True
        finally:
            if not self._authenticated:
                self._clear_session()

    async def get_version(self) -> str:
        return self._text(await self._request("GET", "/api/v2/app/version"), "应用版本")

    async def get_web_api_version(self) -> str:
        return self._text(
            await self._request("GET", "/api/v2/app/webapiVersion"), "Web API 版本"
        )

    async def list_torrents(self) -> list[QbTorrent]:
        page_size = self._TORRENT_PAGE_SIZE
        while True:
            try:
                return await self._list_torrents_paginated(page_size)
            except AppError as exc:
                if (
                    exc.error_code != "QB_RESPONSE_TOO_LARGE"
                    or page_size <= self._TORRENT_MIN_PAGE_SIZE
                ):
                    raise
                page_size = max(self._TORRENT_MIN_PAGE_SIZE, page_size // 2)

    async def find_torrents_by_hashes(
        self, hashes: Collection[str]
    ) -> list[QbTorrent]:
        normalized: set[str] = set()
        for value in hashes:
            info_hash = self._normalize_hash(value)
            normalized.add(info_hash)
            if len(info_hash) == 64:
                normalized.add(info_hash[:40])
        ordered_hashes = sorted(normalized)
        if not ordered_hashes:
            return []

        torrents: dict[str, QbTorrent] = {}
        for start in range(0, len(ordered_hashes), self._HASH_QUERY_BATCH_SIZE):
            batch = ordered_hashes[start : start + self._HASH_QUERY_BATCH_SIZE]
            for torrent in await self._find_torrents_by_hash_batch(batch):
                torrents[torrent.hash] = torrent
        return list(torrents.values())

    async def _find_torrents_by_hash_batch(
        self, hashes: Sequence[str]
    ) -> list[QbTorrent]:
        try:
            payload = self._json(
                await self._request(
                    "GET",
                    "/api/v2/torrents/info",
                    params={
                        "hashes": "|".join(hashes),
                        # One extra result detects an upstream that ignored the filter.
                        "limit": len(hashes) + 1,
                    },
                ),
                list,
            )
        except AppError as exc:
            if exc.error_code != "QB_RESPONSE_TOO_LARGE" or len(hashes) == 1:
                raise
            midpoint = len(hashes) // 2
            return [
                *await self._find_torrents_by_hash_batch(hashes[:midpoint]),
                *await self._find_torrents_by_hash_batch(hashes[midpoint:]),
            ]

        page = self._parse_torrents(payload)
        requested = set(hashes)
        if len(page) > len(hashes) or any(
            not requested.intersection(torrent.identity_hashes) for torrent in page
        ):
            raise AppError(
                "QB_TORRENT_HASH_QUERY_INVALID",
                "qBittorrent 未遵守 info_hash 查询条件",
                status_code=502,
            )
        return page

    async def list_recent_torrents(self, limit: int) -> list[QbTorrent]:
        if not 1 <= limit <= self._RECENT_TORRENT_MAX_ITEMS:
            raise AppError(
                "QB_TORRENT_LIMIT_INVALID",
                "qBittorrent 近期任务查询数量超出允许范围",
                status_code=422,
            )
        payload = self._json(
            await self._request(
                "GET",
                "/api/v2/torrents/info",
                params={
                    "sort": "added_on",
                    "reverse": "true",
                    "limit": limit,
                    "offset": 0,
                },
            ),
            list,
        )
        if len(payload) > limit:
            raise AppError(
                "QB_TORRENT_RECENT_QUERY_INVALID",
                "qBittorrent 未遵守近期任务查询数量限制",
                status_code=502,
            )
        torrents = self._parse_torrents(payload)
        if any(
            current.added_on < following.added_on
            for current, following in pairwise(torrents)
        ):
            raise AppError(
                "QB_TORRENT_RECENT_QUERY_INVALID",
                "qBittorrent 未遵守近期任务排序条件",
                status_code=502,
            )
        return torrents

    async def has_active_seeding(self) -> bool:
        fastest = await self._torrent_probe(
            filter_name="seeding",
            sort="upspeed",
            reverse=True,
            require_complete=True,
        )
        if fastest is not None and self._is_active_seeding(fastest):
            return True

        last_state = await self._torrent_probe(
            filter_name="seeding",
            sort="state",
            reverse=True,
            require_complete=True,
        )
        if last_state is not None and self._is_active_seeding(last_state):
            return True

        return await self._has_forced_up_seeding()

    async def _has_forced_up_seeding(self) -> bool:
        inspected = 0
        boundary_hash: str | None = None
        previous_state: str | None = None
        while inspected < self._FORCED_UP_MAX_ITEMS:
            overlap = 1 if boundary_hash is not None else 0
            request_limit = min(
                self._FORCED_UP_PAGE_SIZE,
                self._FORCED_UP_MAX_ITEMS - inspected + overlap,
            )
            payload = self._json(
                await self._request(
                    "GET",
                    "/api/v2/torrents/info",
                    params={
                        "filter": "seeding",
                        "sort": "state",
                        "reverse": "false",
                        "limit": request_limit,
                        "offset": max(0, inspected - overlap),
                    },
                ),
                list,
            )
            if len(payload) > request_limit:
                raise AppError(
                    "QB_ACTIVE_SEEDING_QUERY_INVALID",
                    "qBittorrent 未遵守活跃做种探测数量限制",
                    status_code=502,
                )
            torrents = self._parse_torrents(payload)
            if boundary_hash is not None:
                if not torrents or torrents[0].hash != boundary_hash:
                    raise AppError(
                        "QB_ACTIVE_SEEDING_QUERY_CHANGED",
                        "qBittorrent 做种任务在探测期间发生变化，请重试",
                        status_code=409,
                        retryable=True,
                    )
                torrents = torrents[1:]

            for torrent in torrents:
                state = torrent.state.casefold()
                if previous_state is not None and state < previous_state:
                    raise AppError(
                        "QB_ACTIVE_SEEDING_QUERY_INVALID",
                        "qBittorrent 未遵守做种任务状态排序条件",
                        status_code=502,
                    )
                if torrent.progress != 1:
                    raise AppError(
                        "QB_ACTIVE_SEEDING_QUERY_INVALID",
                        "qBittorrent 未遵守做种任务筛选条件",
                        status_code=502,
                    )
                if self._is_active_seeding(torrent):
                    return True
                if state > "forcedup":
                    return False
                previous_state = state

            inspected += len(torrents)
            if len(payload) < request_limit:
                return False
            if not torrents:
                raise AppError(
                    "QB_ACTIVE_SEEDING_QUERY_CHANGED",
                    "qBittorrent 做种任务在探测期间发生变化，请重试",
                    status_code=409,
                    retryable=True,
                )
            boundary_hash = torrents[-1].hash

        raise AppError(
            "QB_ACTIVE_SEEDING_QUERY_LIMIT",
            "活跃做种检查达到有界扫描上限",
            status_code=502,
        )

    async def _torrent_probe(
        self,
        *,
        filter_name: str,
        sort: str,
        reverse: bool,
        require_complete: bool = False,
    ) -> QbTorrent | None:
        payload = self._json(
            await self._request(
                "GET",
                "/api/v2/torrents/info",
                params={
                    "filter": filter_name,
                    "sort": sort,
                    "reverse": str(reverse).lower(),
                    "limit": 1,
                    "offset": 0,
                },
            ),
            list,
        )
        if len(payload) > 1:
            raise AppError(
                "QB_ACTIVE_SEEDING_QUERY_INVALID",
                "qBittorrent 未遵守活跃做种探测数量限制",
                status_code=502,
            )
        page = self._parse_torrents(payload)
        torrent = page[0] if page else None
        if require_complete and torrent is not None and torrent.progress != 1:
            raise AppError(
                "QB_ACTIVE_SEEDING_QUERY_INVALID",
                "qBittorrent 未遵守做种任务筛选条件",
                status_code=502,
            )
        return torrent

    @staticmethod
    def _is_active_seeding(torrent: QbTorrent) -> bool:
        return torrent.progress == 1 and (
            torrent.upspeed > 0
            or torrent.state.casefold() in {"uploading", "forcedup"}
        )

    async def _list_torrents_paginated(self, page_size: int) -> list[QbTorrent]:
        torrents: list[QbTorrent] = []
        boundary_hash: str | None = None

        while True:
            # Re-read the previous boundary item so concurrent list changes fail closed.
            offset = max(0, len(torrents) - 1)
            payload = self._json(
                await self._request(
                    "GET",
                    "/api/v2/torrents/info",
                    params={
                        "sort": "hash",
                        "reverse": "false",
                        "limit": page_size,
                        "offset": offset,
                    },
                ),
                list,
            )
            if len(payload) > page_size:
                raise AppError(
                    "QB_TORRENT_PAGINATION_INVALID",
                    "qBittorrent 未遵守任务列表分页大小限制",
                    status_code=502,
                )
            page = self._parse_torrents(payload)

            page_hashes = [item.hash for item in page]
            if any(
                current >= following
                for current, following in pairwise(page_hashes)
            ):
                raise self._unstable_torrent_list()

            if boundary_hash is not None:
                if not page or page[0].hash != boundary_hash:
                    raise self._unstable_torrent_list()
                page = page[1:]

            if page and torrents and page[0].hash <= torrents[-1].hash:
                raise self._unstable_torrent_list()
            if len(torrents) + len(page) > self._TORRENT_MAX_ITEMS:
                raise AppError(
                    "QB_TORRENT_LIST_LIMIT_EXCEEDED",
                    "qBittorrent 任务数量超过安全处理上限",
                    status_code=502,
                )
            torrents.extend(page)

            if len(payload) < page_size:
                return torrents
            if not torrents:
                raise self._unstable_torrent_list()
            boundary_hash = torrents[-1].hash

    @staticmethod
    def _unstable_torrent_list() -> AppError:
        return AppError(
            "QB_TORRENT_LIST_CHANGED",
            "qBittorrent 任务列表在分页读取期间发生变化，请重试",
            status_code=409,
            retryable=True,
        )

    async def get_torrent_files(self, info_hash: str) -> list[QbTorrentFile]:
        normalized = self._normalize_hash(info_hash)
        payload = self._json(
            await self._request(
                "GET", "/api/v2/torrents/files", params={"hash": normalized}
            ),
            list,
        )
        try:
            return [QbTorrentFile.model_validate(item) for item in payload]
        except ValidationError as exc:
            raise self._validation_error("qBittorrent 文件字段校验失败", exc) from exc

    async def get_categories(self) -> dict[str, QbCategory]:
        payload = self._json(
            await self._request("GET", "/api/v2/torrents/categories"), dict
        )
        try:
            return {
                str(name): QbCategory.model_validate({"name": str(name), **value})
                for name, value in payload.items()
                if isinstance(value, dict)
            }
        except ValidationError as exc:
            raise self._validation_error("qBittorrent 分类字段校验失败", exc) from exc

    async def _request(
        self,
        method: str,
        path: str,
        *,
        require_auth: bool = True,
        params: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        before_send: Callable[[], Awaitable[None]] | None = None,
    ) -> httpx.Response:
        normalized_method = method.upper()
        if (normalized_method, path) not in self._ALLOWED_REQUESTS:
            raise AppError(
                "QB_REQUEST_NOT_ALLOWED",
                "qBittorrent 请求不在只读白名单中",
                status_code=405,
            )
        if require_auth and not self._authenticated:
            raise AppError("QB_NOT_AUTHENTICATED", "qBittorrent SID 会话不存在", status_code=401)
        url = urljoin(f"{self._base_url}/", path.lstrip("/"))
        try:
            request = self._client.build_request(
                normalized_method, url, params=params, data=data, files=files
            )
            if self._before_request is not None:
                await self._run_before_send_guard(self._before_request)
            if before_send is not None:
                await self._run_before_send_guard(before_send)
            upstream = await self._client.send(request, stream=True, follow_redirects=False)
            content = bytearray()
            try:
                async for chunk in upstream.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self._max_response_bytes:
                        raise AppError(
                            "QB_RESPONSE_TOO_LARGE",
                            "qBittorrent 响应体超过安全限制",
                            status_code=502,
                        )
            finally:
                await upstream.aclose()
            response = httpx.Response(
                upstream.status_code,
                headers=upstream.headers,
                content=bytes(content),
                request=request,
                extensions=upstream.extensions,
            )
        except httpx.TimeoutException as exc:
            raise AppError(
                "QB_TIMEOUT", "qBittorrent 只读请求超时", status_code=504, retryable=True
            ) from exc
        except httpx.HTTPError as exc:
            raise AppError(
                "QB_NETWORK_ERROR", "无法连接 qBittorrent", status_code=502, retryable=True
            ) from exc
        if response.status_code in {401, 403}:
            self._clear_session()
            raise AppError("QB_AUTH_FAILED", "qBittorrent 登录或 SID 会话无效", status_code=401)
        if not 200 <= response.status_code < 300:
            raise AppError(
                "QB_HTTP_ERROR",
                "qBittorrent 返回了非 2xx 响应",
                status_code=502,
                details={"status_code": response.status_code},
            )
        return response

    @staticmethod
    async def _run_before_send_guard(
        guard: Callable[[], Awaitable[None]],
    ) -> None:
        try:
            await guard()
        except AppError as exc:
            raise AppError(
                exc.error_code,
                exc.message,
                status_code=exc.status_code,
                retryable=exc.retryable,
                details={**exc.details, "external_request_performed": False},
            ) from exc
        except Exception as exc:
            raise AppError(
                "QB_REQUEST_GUARD_FAILED",
                "qBittorrent 请求围栏校验失败",
                status_code=409,
                details={"external_request_performed": False},
            ) from exc

    def _clear_session(self) -> None:
        self._authenticated = False
        self._client.cookies.clear()

    @staticmethod
    def _validate_url(
        value: str, allowed_hosts: tuple[str, ...], allow_insecure_http: bool
    ) -> str:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        allowed_schemes = {"https", "http"} if allow_insecure_http else {"https"}
        if (
            parsed.scheme not in allowed_schemes
            or not host
            or host not in allowed_hosts
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise AppError("QB_URL_NOT_ALLOWED", "qBittorrent 地址不符合安全配置", status_code=400)
        return value

    @staticmethod
    def _looks_like_html(response: httpx.Response) -> bool:
        content_type = response.headers.get("content-type", "").casefold()
        prefix = response.content.lstrip()[:32].lower()
        return "text/html" in content_type or prefix.startswith((b"<!doctype html", b"<html"))

    @classmethod
    def _text(cls, response: httpx.Response, field: str) -> str:
        if cls._looks_like_html(response):
            raise AppError(
                "QB_RESPONSE_INVALID",
                f"qBittorrent {field}返回了 HTML",
                status_code=502,
            )
        value = response.text.strip()
        if not value or len(value) > 64 or any(character in value for character in "<>\r\n"):
            raise AppError("QB_RESPONSE_INVALID", f"qBittorrent {field}格式无效", status_code=502)
        return value

    @classmethod
    def _json(
        cls,
        response: httpx.Response,
        expected: type[list[Any]] | type[dict[str, Any]],
    ) -> Any:
        if cls._looks_like_html(response):
            raise AppError("QB_RESPONSE_INVALID", "qBittorrent 返回了 HTML 页面", status_code=502)
        try:
            payload = response.json()
        except ValueError as exc:
            raise AppError(
                "QB_RESPONSE_INVALID",
                "qBittorrent 响应不是有效 JSON",
                status_code=502,
            ) from exc
        if not isinstance(payload, expected):
            raise AppError("QB_RESPONSE_INVALID", "qBittorrent JSON 结构无效", status_code=502)
        return payload

    @staticmethod
    def _validation_error(message: str, exc: ValidationError) -> AppError:
        fields = sorted({".".join(str(part) for part in error["loc"]) for error in exc.errors()})
        return AppError(
            "QB_RESPONSE_INVALID",
            message,
            status_code=502,
            details={"invalid_fields": fields[:20]},
        )

    @classmethod
    def _parse_torrents(cls, payload: list[Any]) -> list[QbTorrent]:
        try:
            return [QbTorrent.model_validate(item) for item in payload]
        except ValidationError as exc:
            raise cls._validation_error("qBittorrent 种子字段校验失败", exc) from exc

    @classmethod
    def _normalize_hash(cls, value: str) -> str:
        if not cls._INFO_HASH.fullmatch(value):
            raise AppError("QB_INFO_HASH_INVALID", "info_hash 格式无效", status_code=422)
        return value.lower()
